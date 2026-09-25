from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

REQUIRED = {"date", "symbol", "open", "high", "low", "close", "volume"}
ALPACA_CREDENTIALS = {"ALPACA_API_KEY", "ALPACA_API_SECRET"}


class DataValidationError(ValueError):
    pass


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    if len(frame.columns) == 0:
        raise DataValidationError("Bars have no columns.")
    missing = REQUIRED - set(frame.columns.str.lower())
    if missing:
        raise DataValidationError(f"Missing required bar columns: {sorted(missing)}")
    data = frame.copy()
    data.columns = [str(c).lower() for c in data.columns]
    data["date"] = pd.to_datetime(data["date"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
    data["symbol"] = data["symbol"].astype(str).str.upper().str.strip()
    for col in ("open", "high", "low", "close", "volume"):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    if data[list(REQUIRED)].isna().any().any():
        raise DataValidationError("Bars contain missing or non-numeric required values.")
    if (data[["open", "high", "low", "close"]] <= 0).any().any():
        raise DataValidationError("Prices must be positive.")
    if (data["volume"] < 0).any():
        raise DataValidationError("Volume cannot be negative.")
    if (data["high"] < data[["open", "low", "close"]].max(axis=1)).any():
        raise DataValidationError("High is below an open, low, or close.")
    if (data["low"] > data[["open", "high", "close"]].min(axis=1)).any():
        raise DataValidationError("Low is above an open, high, or close.")
    if data.duplicated(["symbol", "date"]).any():
        raise DataValidationError("Duplicate symbol/date bars found.")
    data = data.sort_values(["symbol", "date"]).reset_index(drop=True)
    returns = data.groupby("symbol", sort=False)["close"].pct_change().abs()
    if (returns > 1.0).any():
        raise DataValidationError("Daily return exceeds 100%; inspect splits/corporate actions.")
    return data


def load_csv(path: str | Path) -> pd.DataFrame:
    return validate_bars(pd.read_csv(path))


def load_parquet(path: str | Path) -> pd.DataFrame:
    return validate_bars(pd.read_parquet(path))


def freshness_report(frame: pd.DataFrame, expected_latest_date: str | pd.Timestamp) -> dict[str, object]:
    """Compare observed bars with a caller-supplied expected session date.

    Calendar/session selection is intentionally supplied by the caller because weekends,
    holidays, and exchange-specific calendars cannot safely be inferred here.
    """
    data = validate_bars(frame)
    expected = pd.Timestamp(expected_latest_date).tz_localize(None).normalize()
    latest_by_symbol = data.groupby("symbol").date.max()
    stale = latest_by_symbol[latest_by_symbol < expected]
    return {
        "expected_date": expected.date().isoformat(),
        "latest_date": latest_by_symbol.max().date().isoformat(),
        "stale_symbols": stale.index.tolist(),
        "is_fresh": bool(stale.empty),
    }


def save_raw_bars(frame: pd.DataFrame, root: str | Path = "data/raw/bars") -> Path:
    """Append immutable date partitions; fail instead of silently replacing existing partitions."""
    data = validate_bars(frame)
    root = Path(root)
    written = []
    for (year, month), part in data.groupby([data.date.dt.year, data.date.dt.month]):
        target = root / f"year={year}" / f"month={month:02d}" / "bars.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"Raw partition already exists: {target}")
        part.to_parquet(target, index=False)
        written.append(target)
    return written[0] if len(written) == 1 else root


def _load_local_alpaca_credentials(env_file: str | Path = ".env") -> None:
    """Load only Alpaca credentials from a local .env without overriding process env."""
    path = Path(env_file)
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if name not in ALPACA_CREDENTIALS:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            os.environ.setdefault(name, value)


def fetch_alpaca_daily(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """Fetch daily bars with read-only historical data credentials; never sends orders."""
    _load_local_alpaca_credentials()
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
    if not key or not secret:
        raise RuntimeError("Set ALPACA_API_KEY and ALPACA_API_SECRET in the environment.")
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(key, secret)
    request = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
                               start=pd.Timestamp(start, tz="UTC"), end=pd.Timestamp(end, tz="UTC"),
                               adjustment="all")
    bars = client.get_stock_bars(request).df.reset_index()
    return validate_bars(bars.rename(columns={"timestamp": "date"}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a daily OHLCV CSV file.")
    parser.add_argument("csv", help="CSV with date,symbol,open,high,low,close,volume columns")
    args = parser.parse_args()
    bars = load_csv(args.csv)
    print(f"Valid: {len(bars)} bars, {bars.symbol.nunique()} symbols, "
          f"{bars.date.min().date()} through {bars.date.max().date()}")


if __name__ == "__main__":
    main()
