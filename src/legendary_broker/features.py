"""Point-in-time daily price features and forward excess-return labels."""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = [
    "ret_5", "ret_20", "ret_60", "vol_20", "vol_60", "ma_gap_20", "ma_gap_60",
    "volume_z20", "market_ret_20",
]
BAR_COLUMNS = {"date", "symbol", "close", "volume"}


def make_features(bars: pd.DataFrame, benchmark: str = "SPY", horizon: int = 5) -> pd.DataFrame:
    """Create features observable at the close and a future excess-return training label.

    The output row at date ``t`` uses closes and volume at or before ``t`` for its
    features. ``forward_excess`` is the security's close-to-close return over the
    next ``horizon`` observed bars minus the benchmark return over the same dates.
    The last ``horizon`` observations for each symbol have a missing label.
    """
    missing = BAR_COLUMNS - set(bars.columns)
    if missing:
        raise ValueError(f"Missing feature input columns: {sorted(missing)}")
    if horizon < 1:
        raise ValueError("horizon must be at least one session")
    if bars.duplicated(["symbol", "date"]).any():
        raise ValueError("Input must have at most one bar per symbol and date")

    data = bars.copy()
    data["symbol"] = data["symbol"].astype(str).str.upper().str.strip()
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    data = data.sort_values(["symbol", "date"]).reset_index(drop=True)
    benchmark_rows = data.loc[data.symbol == benchmark.upper(), ["date", "close"]]
    if benchmark_rows.empty:
        raise ValueError(f"Benchmark {benchmark.upper()} is missing from the input bars")
    if benchmark_rows.date.duplicated().any():
        raise ValueError("Benchmark has multiple bars on the same date")

    benchmark = benchmark_rows.set_index("date").close.sort_index()
    market_return_20 = benchmark.pct_change(20)
    benchmark_forward = benchmark.shift(-horizon).div(benchmark).sub(1)
    pieces = []
    for _symbol, group in data.groupby("symbol", sort=False):
        g = group.sort_values("date").copy()
        close = g.close.astype(float)
        returns = close.pct_change()
        for lookback in (5, 20, 60):
            g[f"ret_{lookback}"] = close.pct_change(lookback)
        g["vol_20"] = returns.rolling(20, min_periods=15).std() * np.sqrt(252)
        g["vol_60"] = returns.rolling(60, min_periods=45).std() * np.sqrt(252)
        g["ma_gap_20"] = close.div(close.rolling(20, min_periods=20).mean()).sub(1)
        g["ma_gap_60"] = close.div(close.rolling(60, min_periods=60).mean()).sub(1)
        volume = g.volume.astype(float)
        volume_mean = volume.rolling(20, min_periods=15).mean()
        volume_std = volume.rolling(20, min_periods=15).std()
        g["volume_z20"] = volume.sub(volume_mean).div(volume_std.replace(0, np.nan))
        g["market_ret_20"] = g.date.map(market_return_20)

        future_asset = close.shift(-horizon).div(close).sub(1)
        future_benchmark = g.date.map(benchmark_forward)
        g["forward_excess"] = future_asset - future_benchmark
        pieces.append(g)

    return pd.concat(pieces, ignore_index=True).replace([np.inf, -np.inf], np.nan)
