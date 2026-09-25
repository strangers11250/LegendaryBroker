import unittest

import numpy as np
import pandas as pd

from legendary_broker.features import FEATURES
from legendary_broker.models import (
    InsufficientTrainingData, evaluate_predictions, predict_as_of, walk_forward_predictions,
)


def model_frame(periods=130):
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2025-01-01", periods=periods)
    rows = []
    for symbol_index, symbol in enumerate(("AAA", "BBB", "CCC", "DDD")):
        features = rng.normal(size=(periods, len(FEATURES)))
        labels = 0.01 * features[:, 0] - 0.005 * features[:, 1] + rng.normal(0, 0.01, periods)
        for i, date in enumerate(dates):
            end = dates[i + 5] if i + 5 < periods else pd.NaT
            row = {name: features[i, j] for j, name in enumerate(FEATURES)}
            row.update({"date": date, "label_end_date": end, "symbol": symbol,
                        "forward_excess": labels[i] if pd.notna(end) else np.nan})
            rows.append(row)
    return pd.DataFrame(rows)


class ModelTests(unittest.TestCase):
    def test_prediction_training_uses_only_fully_matured_labels(self):
        frame = model_frame()
        as_of = pd.bdate_range("2025-01-01", periods=100)[-1]
        base = predict_as_of(frame, as_of, min_train_rows=100)
        changed = frame.copy()
        immature = (changed.date < as_of) & (changed.label_end_date > as_of)
        changed.loc[immature, "forward_excess"] = 1000
        altered = predict_as_of(changed, as_of, min_train_rows=100)
        np.testing.assert_allclose(base.prediction, altered.prediction)
        self.assertTrue((base.training_end_date <= as_of).all())

    def test_both_baselines_produce_finite_predictions(self):
        frame = model_frame()
        as_of = pd.bdate_range("2025-01-01", periods=100)[-1]
        for model in ("ridge", "boosted_tree"):
            with self.subTest(model=model):
                prediction = predict_as_of(frame, as_of, model_name=model, min_train_rows=100)
                self.assertEqual(len(prediction), 4)
                self.assertTrue(np.isfinite(prediction.prediction).all())

    def test_walk_forward_predictions_are_out_of_sample(self):
        frame = model_frame()
        predictions = walk_forward_predictions(
            frame, model_name="ridge", step_days=10, min_train_rows=100
        )
        self.assertFalse(predictions.empty)
        self.assertTrue((predictions.training_end_date <= predictions.date).all())
        metrics = evaluate_predictions(predictions)
        self.assertGreater(metrics["observations"], 0)
        self.assertGreaterEqual(metrics["rmse"], 0)

    def test_requires_minimum_mature_training_rows(self):
        frame = model_frame()
        as_of = pd.bdate_range("2025-01-01", periods=20)[-1]
        with self.assertRaises(InsufficientTrainingData):
            predict_as_of(frame, as_of, min_train_rows=100)


if __name__ == "__main__":
    unittest.main()
