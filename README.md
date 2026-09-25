# LegendaryBroker

Research-first quantitative portfolio tooling. Work is delivered in modules so each piece can be reviewed before we add the next layer.

## Module 1: daily market data

The first module validates and normalizes daily OHLCV bars, reads CSV or Parquet, checks freshness against an operator-supplied expected session date, and offers an optional Alpaca historical bars adapter. It never calls brokerage order endpoints.

Required CSV columns:

```text
date,symbol,open,high,low,close,volume
```

Run the validator from the repository root in PowerShell:

```powershell
$env:PYTHONPATH = ".\src"
& ".\.venv\Scripts\python.exe" -m legendary_broker.data .\your_daily_bars.csv
```

Or run the tests using the project environment:

```powershell
$env:PYTHONPATH = ".\src"
& ".\.venv\Scripts\python.exe" -m unittest discover -s tests -v
```

For the optional data adapter, set `ALPACA_API_KEY` and `ALPACA_API_SECRET` in the process environment or in the local ignored `.env` file. Environment variables take precedence. Use separate paper credentials and never commit them. The adapter is read-only and requests adjusted daily bars.

## Data quality boundaries

- The caller supplies the expected market session date; the validator does not guess holidays or market calendars.
- A daily move greater than 100% is rejected for review, which can flag an unhandled split or bad source row.
- The source should provide consistently adjusted prices. This module does not independently reconstruct corporate actions.
- Raw Parquet partitions are immutable: a second write to an existing year/month partition raises an error.

## Module 2: point-in-time features

`legendary_broker.features.make_features` adds 5/20/60-session momentum, 20/60-session annualized volatility, moving-average distance, a volume z-score, and 20-session benchmark return. It also creates a forward excess-return label over the configured horizon. Feature columns use observations through the current close; the label is kept separate as `forward_excess` for later supervised training. The trailing horizon has no label by design.

```python
from legendary_broker.data import load_csv
from legendary_broker.features import make_features

bars = load_csv("daily_bars.csv")
features = make_features(bars, benchmark="SPY", horizon=5)
```

The benchmark must be included in the same input data. Current data validation and the feature pipeline are provider-independent, so later data sources can produce the same normalized bars interface.

Run all module checks with:

```powershell
python -m unittest discover -s tests -v
```

## Module 3: prediction models

The initial estimators are standardized Ridge regression and scikit-learn's histogram gradient boosted tree. `predict_as_of` fits only on observations before the requested date whose `label_end_date` has already arrived. This purges overlapping forward labels from training. The feature pipeline stores that label end date specifically for this check.

Generate periodic out-of-sample predictions from a feature CSV:

```powershell
$env:PYTHONPATH = ".\src"
& ".\.venv\Scripts\python.exe" -m legendary_broker.models .\features.csv --model ridge --step-days 5 --min-train-rows 200
```

The command writes prediction rows and reports MAE, RMSE, and mean daily cross-sectional rank IC. These are research diagnostics; they are not a claim of live performance. Both feature extraction and model fitting accept provider-neutral tabular data.
