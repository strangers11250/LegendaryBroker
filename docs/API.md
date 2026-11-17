# Market lookup API

All endpoints are GET-only, same origin as `/web/`, under `/api/`.
They read SQLite; a web search never calls Alpaca. Dates are inclusive ISO dates.

| Request | Result |
| --- | --- |
| `/api/health` | `{ "status": "ok" }` after a real database read |
| `/api/overview` | `items` for SPY/QQQ/DIA/IWM with symbol, name, close, date, change_percent |
| `/api/symbols?q=AA&limit=20` | `symbols` list with symbol and latest_date; ticker-prefix search |
| `/api/bars/AAPL?start=2025-01-01&end=2025-12-31` | symbol, start, end, bars, latest_date, ingested_at, provenance |
| `/api/status` | interval_hours, running, last_attempt, last_success, last_error, symbols and safe ingestion metadata |

Bars have `date,symbol,open,high,low,close,volume`. Provenance is `source`, `feed`,
`adjustment`. Missing data returns empty bars/null summary fields, never synthetic
prices. `change_percent` compares the two latest available daily closes; one row
has no daily change. `latest_date` refers to the stored symbol, not necessarily the
requested date range. `ingested_at` is the most recent stored record write, while
`last_success` reports a successful refresh even if prices did not change.

Default bar range is one year ending today. Reversed ranges, invalid dates/tickers,
ranges longer than 3660 days, and limits outside 1–5000 receive HTTP 422. Symbol
queries allow at most 15 characters and 100 results. Unknown stock/range returns an
empty dataset. SQL/storage failures receive a sanitized 503; no stack traces or
credentials appear in responses. No CORS wildcard, provider URL, write, trade,
configuration update or arbitrary SQL endpoint exists.

Data endpoints use a bounded 128-entry/60-second server cache, database-revision
invalidation, ETags and conditional GETs. Browser cache max-age is 60 seconds.
The React client additionally deduplicates requests and caches query keys by symbol
and date range; it never shows the previous symbol's data as the new symbol loads.
Health and error responses are not stored. The HTTPS proxy applies 10 requests/
second/IP with a burst of 40; excess requests receive 429.

Example:

```sh
curl 'https://13.184.199.172/api/bars/SPY?start=2026-01-01&end=2026-11-15'
```
