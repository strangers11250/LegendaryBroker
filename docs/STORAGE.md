# Daily market storage convention (schema version 1)

SQLite is the production database for this single-server deployment. The database is
`/var/lib/legendary-market/market.sqlite3`; local default is `data/market.sqlite3`.
Set `BROKER_DB_PATH` to override. SQLite WAL, a 10-second busy timeout, and short
transactions allow the API to read while the independent worker publishes data.
There is one writer. PostgreSQL is a future migration, not a claimed supported backend.
Existing immutable research Parquet partitions and model/feature interfaces are unchanged.

## Records

`bars` is a WITHOUT ROWID table. The primary key is
`(symbol, session_date, source, feed, adjustment)`.

| Field | Convention |
| --- | --- |
| symbol | Uppercase ticker, 1–15 ASCII characters; initial letter then letters, digits, period or hyphen |
| session_date | Exchange session date, ISO `YYYY-MM-DD`, America/New_York |
| source | `alpaca` |
| feed | `sip` (default consolidated historical feed) or explicitly selected `iex` (one exchange) |
| adjustment | `all`: provider adjusts splits, dividends and applicable corporate actions |
| open, high, low, close | Positive finite SQLite REAL values, USD per share, provider precision |
| volume | Finite nonnegative REAL shares; fractional adjusted volumes are accepted |
| ingested_at | UTC ISO timestamp when that stored record was written |

Daily provider timestamps convert to America/New_York **before** extracting the date.
Current New York date is excluded: these are completed daily bars, not live quotes.
No calendar is guessed to claim freshness; latest session and last successful fetch
are distinct values. Weekends and exchange holidays naturally leave the latest session unchanged.
REAL is adequate for market research/charting, not settlement or monetary accounting.

OHLC bounds, finite values, uniqueness and the existing daily-move quality guard
are validated before publishing. SQL CHECK constraints provide another barrier.
Parameterized queries are used throughout. `bars_feed_date` supplements the primary
key for provenance/date operations. `PRAGMA user_version=1` identifies the schema;
unknown newer versions fail closed rather than attempting an implicit migration.

## Publication and retention

Every successful fetch replaces the **entire configured universe for its source/feed/
adjustment in one transaction**, using the rolling `history_days` window (default
1827 calendar days, approximately five years). Old dates and removed symbols for
that feed disappear on the next successful publication. This is intentional retention.
Missing configured symbols, invalid rows, malformed provider responses or provider
failures reject the complete batch and preserve the last successful snapshot.
No rows are fabricated. Search operates only on symbols already stored; add desired
stocks to ingestion configuration to collect them.

Re-fetching the complete retained history repairs historical adjustments after
corporate actions. Mixing newly adjusted values with old adjustment bases would
corrupt research returns. The standalone `upsert_bars` interface remains available
for bounded correction/import use, but the scheduled worker publishes snapshots.

`metadata` holds a monotonically changing revision and JSON ingestion status. Revision
changes invalidate API response caches even when another process writes. Status
contains attempt/success timestamps and sanitized error codes, never credentials.
Status updates and bar snapshots have their own short transactions; `/api/health`
means database readiness, not provider freshness. The dashboard displays fetch status separately.

## Defaults and interpretation

SPY, QQQ, DIA and IWM represent clearly labeled **ETF proxies** for the S&P 500,
Nasdaq-100, Dow Jones and Russell 2000. They are tradable ETF prices, not the index
levels. Initial additional stocks: AAPL, MSFT and NVDA. All returns on the dashboard
are computed from stored, adjusted closing prices.

Provider references: [historical stock bars](https://docs.alpaca.markets/us/reference/stockbars)
and [market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).
