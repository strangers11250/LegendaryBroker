"""Transactional daily-bar store. See docs/STORAGE.md for its data contract."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3

import pandas as pd

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
PROVENANCE_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,31}$")
INDEX_PROXIES = {
    "SPY": "S&P 500 ETF proxy", "QQQ": "Nasdaq-100 ETF proxy",
    "DIA": "Dow Jones ETF proxy", "IWM": "Russell 2000 ETF proxy",
}


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Invalid stock symbol.")
    return symbol


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self, *, readonly: bool = False):
        if readonly:
            connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                         timeout=10)
        else:
            connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported database schema version.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL, session_date TEXT NOT NULL,
                    source TEXT NOT NULL, feed TEXT NOT NULL, adjustment TEXT NOT NULL,
                    open REAL NOT NULL CHECK(open > 0), high REAL NOT NULL CHECK(high > 0),
                    low REAL NOT NULL CHECK(low > 0), close REAL NOT NULL CHECK(close > 0),
                    volume REAL NOT NULL CHECK(volume >= 0), ingested_at TEXT NOT NULL,
                    CHECK(high >= open AND high >= low AND high >= close),
                    CHECK(low <= open AND low <= close),
                    PRIMARY KEY(symbol, session_date, source, feed, adjustment)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS bars_feed_date
                    ON bars(source, feed, adjustment, session_date, symbol);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('revision', '0');
                PRAGMA user_version=1;
            """)

    @staticmethod
    def _bump_revision(db: sqlite3.Connection) -> None:
        db.execute("UPDATE metadata SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) "
                   "WHERE key='revision'")

    def revision(self) -> int:
        with self.connect(readonly=True) as db:
            return int(db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0])

    @staticmethod
    def _prepare_rows(frame: pd.DataFrame, feed: str, source: str, adjustment: str) -> list:
        if not all(PROVENANCE_RE.fullmatch(x) for x in (feed, source, adjustment)):
            raise ValueError("Invalid provenance identifier.")
        required = {"date", "symbol", "open", "high", "low", "close", "volume"}
        if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
            raise ValueError("Bars require date, symbol, OHLC and volume columns.")
        rows, seen = [], set()
        now = utc_now()
        for record in frame.to_dict(orient="records"):
            symbol = normalize_symbol(str(record["symbol"]))
            stamp = pd.Timestamp(record["date"])
            if pd.isna(stamp):
                raise ValueError("Invalid session date.")
            if stamp.tzinfo is not None:
                stamp = stamp.tz_convert("America/New_York")
            session = stamp.date().isoformat()
            if (symbol, session) in seen:
                raise ValueError("Duplicate symbol/session bars in batch.")
            seen.add((symbol, session))
            values = [float(record[name]) for name in ("open", "high", "low", "close", "volume")]
            op, high, low, close, volume = values
            if (not all(math.isfinite(x) for x in values) or min(values[:4]) <= 0
                    or volume < 0 or high < max(op, low, close) or low > min(op, close)):
                raise ValueError("Invalid finite OHLCV values.")
            rows.append((symbol, session, source, feed, adjustment, *values, now))
        return rows

    def replace_history(self, frame: pd.DataFrame, symbols: list[str], feed: str = "sip",
                        source: str = "alpaca", adjustment: str = "all") -> int:
        """Atomically publish one authoritative universe/history for a provenance namespace."""
        rows = self._prepare_rows(frame, feed, source, adjustment)
        expected = {normalize_symbol(symbol) for symbol in symbols}
        if not expected or {row[0] for row in rows} != expected:
            raise ValueError("Snapshot must contain exactly the configured symbols.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM bars WHERE source=? AND feed=? AND adjustment=?",
                       (source, feed, adjustment))
            db.executemany("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self._bump_revision(db)
        return len(rows)

    def upsert_bars(self, frame: pd.DataFrame, feed: str = "sip", source: str = "alpaca",
                    adjustment: str = "all", retain_start: date | None = None,
                    retain_symbols: list[str] | None = None) -> int:
        rows = self._prepare_rows(frame, feed, source, adjustment)
        if not rows:
            return 0
        retained = ([normalize_symbol(value) for value in retain_symbols]
                    if retain_symbols is not None else sorted({row[0] for row in rows}))
        if retain_start is not None and not isinstance(retain_start, date):
            raise ValueError("Retention start must be a date.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = db.total_changes
            db.executemany("""
                INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol,session_date,source,feed,adjustment) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume, ingested_at=excluded.ingested_at
                WHERE bars.open != excluded.open OR bars.high != excluded.high
                    OR bars.low != excluded.low OR bars.close != excluded.close
                    OR bars.volume != excluded.volume
            """, rows)
            written = db.total_changes - before
            if retain_start is not None:
                for symbol in retained:
                    db.execute("DELETE FROM bars WHERE symbol=? AND session_date < ? "
                               "AND source=? AND feed=? AND adjustment=?",
                               (symbol, retain_start.isoformat(), source, feed, adjustment))
            if db.total_changes != before:
                self._bump_revision(db)
            return written

    def bars(self, symbol: str, start: date, end: date, feed: str = "sip",
             limit: int = 5000) -> list[dict]:
        symbol = normalize_symbol(symbol)
        if start > end or (end - start).days > 3660 or not 1 <= limit <= 5000:
            raise ValueError("Invalid or excessive date range/limit.")
        with self.connect(readonly=True) as db:
            return [dict(row) for row in db.execute("""
                SELECT session_date AS date, symbol, open, high, low, close, volume
                FROM bars WHERE symbol=? AND session_date BETWEEN ? AND ?
                    AND source='alpaca' AND feed=? AND adjustment='all'
                ORDER BY session_date LIMIT ?
            """, (symbol, start.isoformat(), end.isoformat(), feed, limit))]

    def symbols(self, query: str = "", limit: int = 20, feed: str = "sip") -> list[dict]:
        query = query.strip().upper()
        if query and not SYMBOL_RE.fullmatch(query):
            raise ValueError("Invalid symbol search.")
        if not 1 <= limit <= 100:
            raise ValueError("Invalid symbol limit.")
        with self.connect(readonly=True) as db:
            return [dict(row) for row in db.execute("""
                SELECT symbol, max(session_date) AS latest_date FROM bars
                WHERE symbol LIKE ? AND source='alpaca' AND feed=? AND adjustment='all'
                GROUP BY symbol ORDER BY symbol LIMIT ?
            """, (query + "%", feed, limit))]

    def bar_metadata(self, symbol: str | None = None, feed: str = "sip") -> dict:
        sql = """SELECT max(session_date) AS latest_date, max(ingested_at) AS ingested_at
                 FROM bars WHERE source='alpaca' AND feed=? AND adjustment='all'"""
        params = [feed]
        if symbol is not None:
            sql += " AND symbol=?"
            params.append(normalize_symbol(symbol))
        with self.connect(readonly=True) as db:
            return dict(db.execute(sql, params).fetchone())

    def overview(self, feed: str = "sip") -> list[dict]:
        items = []
        with self.connect(readonly=True) as db:
            for symbol, name in INDEX_PROXIES.items():
                rows = db.execute("""
                    SELECT session_date, close FROM bars WHERE symbol=? AND source='alpaca'
                        AND feed=? AND adjustment='all' ORDER BY session_date DESC LIMIT 2
                """, (symbol, feed)).fetchall()
                items.append({"symbol": symbol, "name": name,
                              "close": rows[0]["close"] if rows else None,
                              "date": rows[0]["session_date"] if rows else None,
                              "change_percent": ((rows[0]["close"] / rows[1]["close"] - 1) * 100
                                                 if len(rows) == 2 else None)})
        return items

    def get_status(self) -> dict:
        with self.connect(readonly=True) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='ingestion_status'").fetchone()
            return json.loads(row[0]) if row else {}

    def update_status(self, **values) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM metadata WHERE key='ingestion_status'").fetchone()
            status = json.loads(row[0]) if row else {}
            if all(key in status and status[key] == value for key, value in values.items()):
                return
            status.update(values)
            db.execute("INSERT INTO metadata(key,value) VALUES ('ingestion_status',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(status, allow_nan=False),))
            self._bump_revision(db)

