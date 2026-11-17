# Market service security boundary

The dashboard and historical lookup API intentionally expose read-only market data
over the server's existing HTTPS listener. They do not expose accounts, holdings,
trading operations or provider credentials. There is no administrative HTTP route.
Changing the ingestion universe or frequency requires server filesystem access.
If account data is added later, authentication/authorization must precede those routes.
The operator is responsible for their provider's market-data display/distribution
entitlements; IEX and SIP have different coverage and licensing conditions.

Alpaca credentials exist only in `/etc/legendary-market/market.env`, owner root,
mode 0600; systemd reads them into the ingestion process. They never enter the API
environment, frontend build, archive, Git history, API responses or logs. Local `.env`
remains ignored. Use separate paper/read-only data credentials. No trading endpoint
is invoked. Provider errors are mapped to a small set of safe codes rather than
logging HTTP bodies or request headers.

The dedicated `legendary-market` system user has no login shell. Only its private
state directory is writable under systemd `ProtectSystem=strict`; home access is
disabled, privileges cannot be elevated, and API/worker memory and CPU are capped.
Uvicorn binds only `127.0.0.1:8765` and trusts forwarded headers only from loopback.
Existing Nginx handles TLS and per-IP API rate limits. Browser responses add CSP,
nosniff, same-origin referrer policy and frame denial. No external scripts/fonts are
needed. SQL is parameterized and requests/results are bounded.

SQLite resides outside the web root with directory mode 0750 and umask 0027. WAL
sidecar files must be retained with the database. Use SQLite's backup interface,
not a raw copy of an active WAL database. Deployment snapshots are root-readable
only. The production user owns state, not source or service configuration.

CI runs tests on PRs without deployment secrets. All Actions are pinned to exact
revisions, `GITHUB_TOKEN` has contents-read only, and deployment occurs only after
the verification job passes on main with `MARKET_DEPLOY_ENABLED=true`. Use a
dedicated GitHub production environment and deployment identity, pinned SSH host
keys, and no brokerage credentials in GitHub. Do not reuse the server's personal
SSH identity. The installer supports sudo and changes only market-owned units,
paths and an include in the existing HTTPS site. The supplied CI SSH identity needs
deployment sudo access; its compromise would grant server control. Configure
environment protection and narrowly scope repository access accordingly.
