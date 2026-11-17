# Change log

## 2026-11-16 — market data platform

- Added schema-versioned SQLite daily-bar storage, transactional snapshot publication,
  query indexes, validation and independent ingestion status metadata.
- Added read-only FastAPI health, symbol lookup, date-bounded bars, ETF-proxy overview
  and ingestion status endpoints with bounded revision-aware caching and ETags.
- Added periodic Alpaca ingestion, explicit historical SIP/IEX feeds, completed New
  York sessions, full retained-history adjustment corrections, atomic rolling retention,
  retry/time/response bounds, cross-process locking and reloadable hourly frequency.
- Replaced the website welcome source with a React/TypeScript dashboard while
  preserving the original as web/welcome.html. Added ETF cards, symbol/date search,
  charts/tables, cancellation, cache reuse and errors/empty states.
- Preserved the separate website Git history and linked exact frontend revisions
  through a parent submodule for coherent releases.
- Added pinned runtime/test dependencies, GitHub Actions checks and an optional
  main deployment job consuming the verified artifact, server units, Nginx includes,
  release packaging, readiness checks, backup and rollback handling.
- Added storage/API/security/operations documentation. Existing validators, feature
  engineering, prediction models, Parquet behavior and Discord service are preserved.
- Validation and final deployment evidence are recorded in docs/VERIFICATION.md.
- Hardened post-reload readiness to tolerate Nginx worker handoff and verify full
  public HTTPS behavior; corrected first-install rollback ordering for service links.
