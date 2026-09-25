"""Leakage-aware supervised return models and out-of-sample evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURES

REQUIRED = {"date", "label_end_date", "symbol", "forward_excess", *FEATURES}
MODEL_NAMES = {"ridge", "boosted_tree"}


class InsufficientTrainingData(ValueError):
    """Raised when too few fully matured training labels are available."""


def _normalize_dates(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"Missing model input columns: {sorted(missing)}")
    data = frame.copy()
    data["date"] = pd.to_datetime(data.date, errors="raise").dt.tz_localize(None).dt.normalize()
    data["label_end_date"] = pd.to_datetime(
        data.label_end_date, errors="coerce"
    ).dt.tz_localize(None).dt.normalize()
    return data


def build_model(name: str = "ridge"):
    """Build a model pipeline; imputation/scaling are fitted on training rows only."""
    if name == "ridge":
        return make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=10.0)
        )
    if name == "boosted_tree":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingRegressor(
                max_iter=100, max_leaf_nodes=15, l2_regularization=1.0, random_state=17
            ),
        )
    raise ValueError(f"Unknown model {name!r}; choose from {sorted(MODEL_NAMES)}")


def predict_as_of(
    features: pd.DataFrame,
    as_of: str | pd.Timestamp,
    model_name: str = "ridge",
    min_train_rows: int = 200,
) -> pd.DataFrame:
    """Fit on labels fully known by ``as_of`` and predict the cross-section on that date."""
    if min_train_rows < 1:
        raise ValueError("min_train_rows must be positive")
    data = _normalize_dates(features)
    date = pd.Timestamp(as_of).tz_localize(None).normalize()
    train = data.loc[
        (data.date < date)
        & (data.label_end_date <= date)
        & data.forward_excess.notna()
    ].dropna(subset=["forward_excess"])
    current = data.loc[data.date == date].copy()
    if current.empty:
        raise ValueError(f"No feature rows found for prediction date {date.date()}")
    if len(train) < min_train_rows:
        raise InsufficientTrainingData(
            f"Need {min_train_rows} matured training rows before {date.date()}; found {len(train)}"
        )
    estimator = build_model(model_name)
    estimator.fit(train[FEATURES], train.forward_excess.clip(-0.5, 0.5))
    current["prediction"] = estimator.predict(current[FEATURES])
    current["model"] = model_name
    current["training_rows"] = len(train)
    current["training_end_date"] = train.label_end_date.max()
    return current[["date", "symbol", "prediction", "model", "training_rows", "training_end_date"]]


def walk_forward_predictions(
    features: pd.DataFrame,
    model_name: str = "ridge",
    step_days: int = 5,
    min_train_rows: int = 200,
) -> pd.DataFrame:
    """Produce periodic out-of-sample predictions using only labels matured by each date."""
    if step_days < 1:
        raise ValueError("step_days must be positive")
    data = _normalize_dates(features)
    dates = sorted(data.date.dropna().unique())
    outputs = []
    for date in dates:
        matured = data.loc[
            (data.date < date) & (data.label_end_date <= date) & data.forward_excess.notna()
        ]
        if len(matured) >= min_train_rows:
            outputs.append(pd.Timestamp(date))
    outputs = outputs[::step_days]
    predictions = [predict_as_of(data, date, model_name, min_train_rows) for date in outputs]
    if not predictions:
        return pd.DataFrame(columns=["date", "symbol", "prediction", "model", "training_rows",
                                     "training_end_date", "forward_excess"])
    result = pd.concat(predictions, ignore_index=True)
    labels = data[["date", "symbol", "forward_excess", "label_end_date"]]
    return result.merge(labels, on=["date", "symbol"], how="left", validate="one_to_one")


def evaluate_predictions(predictions: pd.DataFrame) -> dict[str, float | int]:
    """Summarize out-of-sample errors and mean daily cross-sectional Spearman IC."""
    valid = predictions.dropna(subset=["prediction", "forward_excess"])
    if valid.empty:
        return {"observations": 0, "mean_rank_ic": float("nan"), "mae": float("nan"),
                "rmse": float("nan")}
    daily_ic = valid.groupby("date").apply(
        lambda day: day.prediction.corr(day.forward_excess, method="spearman")
        if len(day) >= 3 else np.nan,
        include_groups=False,
    ).dropna()
    errors = valid.prediction - valid.forward_excess
    return {
        "observations": int(len(valid)),
        "mean_rank_ic": float(daily_ic.mean()) if not daily_ic.empty else float("nan"),
        "mae": float(errors.abs().mean()),
        "rmse": float(np.sqrt(np.square(errors).mean())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate purged walk-forward model predictions.")
    parser.add_argument("features_csv", help="CSV created by make_features")
    parser.add_argument("--model", choices=sorted(MODEL_NAMES), default="ridge")
    parser.add_argument("--step-days", type=int, default=5)
    parser.add_argument("--min-train-rows", type=int, default=200)
    parser.add_argument("--output", default="data/processed/predictions.csv")
    args = parser.parse_args()
    features = pd.read_csv(args.features_csv, parse_dates=["date", "label_end_date"])
    predictions = walk_forward_predictions(
        features, args.model, args.step_days, args.min_train_rows
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output, index=False)
    print(json.dumps({"metrics": evaluate_predictions(predictions),
                      "predictions_file": str(output)}, indent=2, allow_nan=True))


if __name__ == "__main__":
    main()
