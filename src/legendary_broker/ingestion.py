"""Read-only Alpaca daily-bar ingestion, separate from the web server.

Every refresh atomically replaces the complete configured symbol/history snapshot. This
keeps ``adjustment=all`` history consistent after splits and dividend adjustments.
No brokerage trading endpoint is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import threading
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from .data import DataValidationError, REQUIRED, _load_local_alpaca_credentials, validate_bars
from .storage import Database

LOGGER = logging.getLogger(__name__)
NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_SYMBOLS = ("SPY", "QQQ", "DIA", "IWM", "AAPL", "MSFT", "NVDA")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


@dataclass(frozen=True)
class IngestionConfig:
    interval_hours: float = 6.0
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    feed: str = "sip"
    history_days: int = 1827

    def __post_init__(self) -> None:
        if (not isinstance(self.interval_hours, (int, float))
                or isinstance(self.interval_hours, bool) or not math.isfinite(self.interval_hours)
                or not 0.25 <= self.interval_hours <= 8760):
            raise ValueError("interval_hours must be between 0.25 and 8760")
        if (not isinstance(self.symbols, (list, tuple)) or not self.symbols
                or not all(isinstance(item, str) for item in self.symbols)
                or len(self.symbols) > 100
                or len(set(self.symbols)) != len(self.symbols)
                or any(not SYMBOL.fullmatch(item) for item in self.symbols)):
            raise ValueError("symbols must contain 1-100 unique uppercase stock tickers")
        if self.feed not in {"sip", "iex"}:
            raise ValueError("feed must be sip or iex")
        if (not isinstance(self.history_days, int) or isinstance(self.history_days, bool)
                or not 30 <= self.history_days <= 3650):
            raise ValueError("history_days must be between 30 and 3650")


def load_config(path: str | Path | None = None) -> IngestionConfig:
    """Reload operator JSON settings; environment variables take precedence."""
    selected = path or os.getenv("BROKER_INGESTION_CONFIG")
    raw = json.loads(Path(selected).read_text(encoding="utf-8")) if selected else {}
    if not isinstance(raw, dict) or set(raw) - {"interval_hours", "symbols", "feed", "history_days"}:
        raise ValueError("Unknown ingestion configuration fields")
    symbols = os.getenv("BROKER_SYMBOLS", raw.get("symbols", DEFAULT_SYMBOLS))
    if isinstance(symbols, str):
        symbols = symbols.split(",")
    if not isinstance(symbols, (list, tuple)) or not all(isinstance(s, str) for s in symbols):
        raise ValueError("symbols must be a list or comma-separated string")
    history_days = os.getenv("BROKER_HISTORY_DAYS", raw.get("history_days", 1827))
    if (isinstance(history_days, bool)
            or not isinstance(history_days, (int, str))
            or not re.fullmatch(r"[0-9]+", str(history_days))):
        raise ValueError("history_days must be an integer")
    interval = os.getenv("BROKER_FETCH_INTERVAL_HOURS", raw.get("interval_hours", 6))
    if isinstance(interval, bool):
        raise ValueError("interval_hours must be numeric")
    feed = os.getenv("BROKER_FEED", raw.get("feed", "sip"))
    if not isinstance(feed, str):
        raise ValueError("feed must be sip or iex")
    return IngestionConfig(
        interval_hours=float(interval),
        symbols=tuple(s.strip().upper() for s in symbols),
        feed=feed.lower(),
        history_days=int(history_days),
    )


class ProviderError(RuntimeError):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


def completed_bars(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Map source timestamps to NY session dates before applying daily validation.

    ``end`` is exclusive. Rows from today's session are deliberately never stored.
    """
    if frame.empty:
        return pd.DataFrame(columns=sorted(REQUIRED))
    data = frame.copy()
    timestamps = pd.to_datetime(data.pop("timestamp"), utc=True, errors="raise")
    data["date"] = timestamps.dt.tz_convert(NEW_YORK).dt.tz_localize(None).dt.normalize()
    data = data[(data.date.dt.date >= start) & (data.date.dt.date < end)]
    return validate_bars(data) if not data.empty else pd.DataFrame(columns=sorted(REQUIRED))


def fetch_daily(config: IngestionConfig, start: date, end: date,
                now: datetime) -> pd.DataFrame:
    """Fetch every paginated historical bar with a 30s per-request timeout.

    The historical SIP end is always at least 16 minutes old to support delayed
    historical entitlements. Never accepts a provider URL from configuration.
    """
    _load_local_alpaca_credentials()
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
    if not key or not secret:
        raise ProviderError("credentials_missing")
    end_utc = min(datetime.combine(end, time.min, NEW_YORK).astimezone(timezone.utc),
                  now.astimezone(timezone.utc) - timedelta(minutes=16))
    params = {
        "symbols": ",".join(config.symbols), "timeframe": "1Day",
        "start": datetime.combine(start, time.min, NEW_YORK).isoformat(),
        "end": end_utc.isoformat(), "adjustment": "all", "feed": config.feed,
        "limit": "10000", "sort": "asc",
    }
    rows: list[dict] = []
    tokens: set[str] = set()
    for _page in range(100):
        request = Request("https://data.alpaca.markets/v2/stocks/bars?" + urlencode(params),
                          headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read(32 * 1024 * 1024 + 1)
                if len(payload) > 32 * 1024 * 1024:
                    raise ProviderError("provider_response_too_large")
                result = json.loads(payload)
        except HTTPError as exc:
            code = "provider_authorization_failed" if exc.code in {401, 403} else "provider_http_error"
            raise ProviderError(code, transient=exc.code == 429 or exc.code >= 500) from None
        except (URLError, TimeoutError, OSError):
            raise ProviderError("provider_unavailable", transient=True) from None
        except (ValueError, TypeError):
            raise ProviderError("invalid_provider_response") from None
        try:
            for symbol, bars in (result.get("bars") or {}).items():
                if symbol not in config.symbols:
                    raise ProviderError("unexpected_provider_symbol")
                for bar in bars:
                    rows.append({"symbol": symbol, "timestamp": bar["t"],
                                 "open": bar["o"], "high": bar["h"], "low": bar["l"],
                                 "close": bar["c"], "volume": bar["v"]})
                    if len(rows) > 500_000:
                        raise ProviderError("provider_response_too_large")
            token = result.get("next_page_token")
            if not token:
                return completed_bars(pd.DataFrame(rows), start, end)
            if token in tokens or not isinstance(token, str):
                raise ProviderError("invalid_provider_pagination")
            tokens.add(token)
            params["page_token"] = token
        except (KeyError, TypeError, AttributeError):
            raise ProviderError("invalid_provider_response") from None
    raise ProviderError("provider_pagination_limit")


@contextmanager
def worker_lock(path: str | Path) -> Iterator[bool]:
    """An OS lock survives no process: abnormal termination releases it safely."""
    target = Path(str(path) + ".ingestion.lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    acquired = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, OSError):
            pass
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def run_once(db: Database, config: IngestionConfig, *,
             fetcher: Callable = fetch_daily, now: datetime | None = None,
             stop: threading.Event | None = None) -> bool:
    """Publish one fully validated batch or preserve the last successful batch."""
    now = now or datetime.now(timezone.utc)
    stop = stop or threading.Event()
    with worker_lock(db.path) as acquired:
        if not acquired:
            LOGGER.info("ingestion_already_running")
            return True
        db.update_status(last_attempt=now.isoformat(), running=True, last_error=None,
                         interval_hours=config.interval_hours, symbols=list(config.symbols),
                         missing_symbols=[], feed=config.feed, history_days=config.history_days)
        try:
            end = now.astimezone(NEW_YORK).date()
            start = end - timedelta(days=config.history_days)
            for attempt in range(3):
                try:
                    frame = fetcher(config, start, end, now)
                    break
                except ProviderError as exc:
                    if not exc.transient or attempt == 2:
                        raise
                    if stop.wait(2 ** (attempt + 1)):
                        raise ProviderError("interrupted") from None
            frame = validate_bars(frame)
            if frame.empty:
                raise ProviderError("empty_provider_response")
            if not set(frame.symbol).issubset(config.symbols):
                raise ProviderError("unexpected_provider_symbol")
            if (frame.date.dt.date < start).any() or (frame.date.dt.date >= end).any():
                raise ProviderError("invalid_session_dates")
            missing = sorted(set(config.symbols) - set(frame.symbol))
            if missing:
                db.update_status(missing_symbols=missing)
                raise ProviderError("configured_symbols_missing")
            if stop.is_set():
                raise ProviderError("interrupted")
            written = db.replace_history(frame, symbols=list(config.symbols), feed=config.feed,
                                         source="alpaca", adjustment="all")
            db.update_status(last_success=datetime.now(timezone.utc).isoformat(),
                             last_error=None, running=False, rows_written=written)
            LOGGER.info("ingestion_complete rows=%d feed=%s", written, config.feed)
            return True
        except Exception as exc:
            # Never expose provider response bodies, URLs, or credentials in logs/API.
            error = exc.code if isinstance(exc, ProviderError) else (
                "data_validation_failed" if isinstance(exc, (DataValidationError, ValueError))
                else "ingestion_failed")
            db.update_status(last_error=error, running=False)
            LOGGER.error("ingestion_failed code=%s", error)
            return False


def run_worker(db: Database, config_path: str | Path | None,
               stop: threading.Event) -> None:
    """Poll configuration every 30 seconds; schedule from the last attempt start."""
    last_attempt: datetime | None = None
    while not stop.is_set():
        try:
            config = load_config(config_path)
        except (OSError, ValueError, TypeError):
            db.update_status(last_error="configuration_error", running=False)
            LOGGER.error("configuration_error")
            stop.wait(30)
            continue
        db.update_status(interval_hours=config.interval_hours)
        now = datetime.now(timezone.utc)
        remaining = (0.0 if last_attempt is None else
                     config.interval_hours * 3600 - (now - last_attempt).total_seconds())
        if remaining <= 0:
            last_attempt = now
            run_once(db, config, now=now, stop=stop)
        else:
            stop.wait(min(30, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh completed Alpaca daily bars.")
    parser.add_argument("--once", action="store_true", help="Run one refresh and exit")
    parser.add_argument("--config", help="Optional hot-reloaded JSON configuration file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = Database(os.getenv("BROKER_DB_PATH", "data/market.sqlite3"))
    db.initialize()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if args.once:
        try:
            config = load_config(args.config)
        except (OSError, ValueError, TypeError):
            LOGGER.error("configuration_error")
            return 2
        return 0 if run_once(db, config, stop=stop) else 1
    run_worker(db, args.config, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
