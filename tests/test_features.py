import unittest

import numpy as np
import pandas as pd

from legendary_broker.features import FEATURES, make_features


def synthetic_bars(periods=100):
    dates = pd.bdate_range("2025-01-01", periods=periods)
    rows = []
    for symbol, drift in (("SPY", 0.001), ("AAA", 0.0015), ("BBB", -0.0002)):
        close = 100 * (1 + drift) ** np.arange(periods)
        for date, price, idx in zip(dates, close, range(periods)):
            rows.append({"date": date, "symbol": symbol, "close": price,
                         "volume": 1000 + idx * 3})
    return pd.DataFrame(rows)


class FeaturePipelineTests(unittest.TestCase):
    def test_features_use_past_data_and_label_uses_future_horizon(self):
        bars = synthetic_bars()
        result = make_features(bars, benchmark="SPY", horizon=5)
        row = result[(result.symbol == "AAA") & (result.date == bars.date.unique()[70])].iloc[0]
        self.assertAlmostEqual(row.forward_excess,
                               (bars[(bars.symbol == "AAA")].iloc[75].close /
                                bars[(bars.symbol == "AAA")].iloc[70].close - 1) -
                               (bars[(bars.symbol == "SPY")].iloc[75].close /
                                bars[(bars.symbol == "SPY")].iloc[70].close - 1))
        self.assertTrue(all(name in result.columns for name in FEATURES))
        self.assertTrue(result[(result.symbol == "AAA")].tail(5).forward_excess.isna().all())
        self.assertTrue(result[(result.symbol == "AAA")].tail(5).label_end_date.isna().all())

    def test_future_price_change_does_not_change_past_features(self):
        bars = synthetic_bars()
        baseline = make_features(bars)
        changed = bars.copy()
        changed.loc[(changed.symbol == "AAA") & (changed.date > "2025-03-01"), "close"] *= 2
        after = make_features(changed)
        key = (baseline.symbol == "AAA") & (baseline.date <= "2025-03-01")
        left = baseline.loc[key, FEATURES].reset_index(drop=True)
        right = after.loc[key, FEATURES].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)

    def test_rejects_missing_benchmark(self):
        bars = synthetic_bars().query("symbol != 'SPY'")
        with self.assertRaisesRegex(ValueError, "Benchmark SPY"):
            make_features(bars)

    def test_rejects_nonpositive_horizon(self):
        with self.assertRaisesRegex(ValueError, "horizon"):
            make_features(synthetic_bars(), horizon=0)


if __name__ == "__main__":
    unittest.main()
