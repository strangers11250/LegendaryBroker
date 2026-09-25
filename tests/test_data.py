import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from legendary_broker.data import (
    DataValidationError, _load_local_alpaca_credentials, freshness_report, validate_bars,
)


def sample_bars():
    return pd.DataFrame([
        {"date": "2026-09-22", "symbol": "spy", "open": 100, "high": 102,
         "low": 99, "close": 101, "volume": 1000},
        {"date": "2026-09-23", "symbol": "spy", "open": 101, "high": 103,
         "low": 100, "close": 102, "volume": 1100},
    ])


class ValidateBarsTests(unittest.TestCase):
    def test_normalizes_symbol_sorts_and_parses_dates(self):
        result = validate_bars(sample_bars().iloc[::-1])
        self.assertEqual(result.symbol.tolist(), ["SPY", "SPY"])
        self.assertTrue(result.date.is_monotonic_increasing)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(result.date))

    def test_rejects_duplicate_symbol_date(self):
        data = pd.concat([sample_bars(), sample_bars().iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(DataValidationError, "Duplicate"):
            validate_bars(data)

    def test_rejects_invalid_ohlc(self):
        data = sample_bars()
        data.loc[0, "high"] = 98
        with self.assertRaisesRegex(DataValidationError, "High"):
            validate_bars(data)

    def test_rejects_nonpositive_prices(self):
        data = sample_bars()
        data.loc[0, "close"] = 0
        with self.assertRaisesRegex(DataValidationError, "positive"):
            validate_bars(data)

    def test_rejects_unadjusted_split_like_move(self):
        data = sample_bars()
        data.loc[1, ["open", "high", "low", "close"]] = [201, 207, 199, 205]
        with self.assertRaisesRegex(DataValidationError, "corporate actions"):
            validate_bars(data)

    def test_reports_stale_symbols(self):
        data = pd.concat([sample_bars(), sample_bars().assign(symbol="QQQ").iloc[[0]]])
        report = freshness_report(data, "2026-09-23")
        self.assertFalse(report["is_fresh"])
        self.assertEqual(report["stale_symbols"], ["QQQ"])

    def test_loads_alpaca_credentials_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                'ALPACA_API_KEY="test-key"\nALPACA_API_SECRET=test-secret\nOTHER=value\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"ALPACA_API_SECRET": "process-secret"}, clear=True):
                _load_local_alpaca_credentials(env_file)
                self.assertEqual(os.environ["ALPACA_API_KEY"], "test-key")
                self.assertEqual(os.environ["ALPACA_API_SECRET"], "process-secret")
                self.assertNotIn("OTHER", os.environ)


if __name__ == "__main__":
    unittest.main()
