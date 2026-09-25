from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    capital: float = 3000.0
    symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV")
    benchmark: str = "SPY"
    horizon_days: int = 5
    rebalance_every_days: int = 5
    max_weight: float = 0.10
    max_gross_exposure: float = 0.95
    max_sector_exposure: float = 0.30
    cash_floor: float = 0.05
    max_turnover: float = 0.50
    commission_bps: float = 1.0
    slippage_bps: float = 5.0
    risk_lookback_days: int = 20
    min_history_days: int = 126
    data_timezone: str = "America/New_York"

    @classmethod
    def from_file(cls, path: str | Path) -> "Settings":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        raw["symbols"] = tuple(raw["symbols"])
        settings = cls(**raw)
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.capital <= 0 or self.horizon_days < 1 or self.rebalance_every_days < 1:
            raise ValueError("Capital and day horizons must be positive.")
        if not 0 < self.max_weight <= self.max_gross_exposure <= 1:
            raise ValueError("Require 0 < max_weight <= max_gross_exposure <= 1.")
        if self.cash_floor < 0 or self.max_gross_exposure + self.cash_floor > 1.000001:
            raise ValueError("Cash floor plus maximum exposure cannot exceed 100%.")
        if not 0 < self.max_sector_exposure <= 1 or not 0 <= self.max_turnover <= 2:
            raise ValueError("Invalid sector or turnover limit.")
