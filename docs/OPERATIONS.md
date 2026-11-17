# Run and deploy

## Local development

Use Python 3.12+ and Node 22. Install the reproducible verification environment,
then build the frontend. Existing research dependencies remain in `pyproject.toml`.

```powershell
git clone --recurse-submodules https://github.com/strangers11250/LegendaryBroker.git
cd LegendaryBroker
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-test.lock
$env:PYTHONPATH = 'src'
./.venv/Scripts/python.exe -m legendary_broker.ingestion --once --config config/ingestion.json
cd web
npm ci
npm test
npm run build
cd ..
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m uvicorn legendary_broker.api:app --host 127.0.0.1 --port 8000
```

Set `ALPACA_API_KEY` and `ALPACA_API_SECRET` in ignored local `.env` or the process
environment before ingestion. Open `http://127.0.0.1:8000/web/`. Without credentials,
API and frontend still run with an empty database. In another terminal, run the
worker without `--once` for periodic collection. `npm run dev` proxies API requests
to localhost:8000.

`web` is the existing independent website repository, registered as a submodule.
Commit/push frontend changes to that repository first, then commit its new pointer
in LegendaryBroker. CI checks out the pinned frontend commit. Do not deploy a parent
pointer whose frontend commit has not been pushed. `git submodule update --init`
restores the pinned frontend version after cloning/pulling.

## Production layout

| Path | Purpose |
| --- | --- |
| `/opt/legendary-market/releases/<id>` | Immutable source, prebuilt frontend, release-specific Python environment |
| `/opt/legendary-market/current` | Active release symlink |
| `/var/lib/legendary-market/market.sqlite3` | Private persistent database and WAL sidecars |
| `/etc/legendary-market/market.env` | Root-owned 0600 Alpaca credentials, read only by worker systemd unit |
| `/etc/legendary-market/ingestion.json` | Operator cadence/universe/feed/history configuration |
| `/etc/legendary-market/api.env` | Optional non-secret API feed override, e.g. `BROKER_FEED=iex` |
| `/opt/legendary-market/backups/<timestamp>` | Pre-deployment SQLite/config snapshots and probe logs |

Production services are `legendary-market-api` (loopback port 8765) and
`legendary-market-ingest` (independent polling loop). Both run as `legendary-market`.
Existing Nginx HTTPS site `my-https` gains only a market-owned include routing
`/web/` and `/api/`. The HTTP listener, original `/` welcome page, TLS configuration,
and `discord-bot.service` are preserved. CPU and memory caps accommodate the
existing roughly 1 GB host. No Node process is required on the host.

Change `interval_hours` (0.25–8760, fractional hours allowed) in the server's
ingestion.json. The worker reloads it within 30 seconds, including during its wait.
Defaults: 6 hours, 1827 history days. Environment variables override JSON; changing
environment requires restarting the worker. Changing symbols/history takes effect
at the next scheduled fetch. Change feed in both worker config and API `api.env`,
then restart both services. Different feeds are retained separately and never mixed.
Do not change feed while expecting old-feed data to appear as the new feed.

```sh
sudo systemctl status legendary-market-api legendary-market-ingest
sudo journalctl -u legendary-market-api -u legendary-market-ingest --since '1 hour ago'
curl https://13.184.199.172/api/status
sudo systemctl restart legendary-market-ingest  # runs a refresh immediately
```

Missing configured symbols reject a refresh. Remove unavailable/delisted tickers or
choose a provider/feed with coverage; inspect `/api/status` for missing_symbols.
Provider authorization failures need credential/entitlement correction. No automatic
fallback silently switches feed. Last good data remains available on refresh failure.

## Release procedure

All backend tests, frontend tests/typecheck/build and real-provider preflight must
pass before initial deployment. Subsequent CI releases use the same verified build
artifact. The installer additionally probes an isolated scratch database and built
webpage on loopback port 18765 before changing production. It backs up active SQLite
using its online backup API, swaps only the market release, checks API readiness
and a successful worker refresh, validates Nginx, gracefully reloads Nginx, verifies
HTTPS endpoints and confirms Discord's PID and unit file remain unchanged.
Errors after switching restore prior symlink/routing/config and restart only market
services. Market configuration and credentials are provisioned once and never
overwritten by application releases.

```sh
python deploy/build_release.py
scp dist/market-release.tar.gz aws1:/tmp/market-release.tar.gz
# On the host, choose a unique release ID; do not overwrite a prior release.
sudo mkdir -p /opt/legendary-market/releases/RELEASE_ID
sudo tar -xzf /tmp/market-release.tar.gz -C /opt/legendary-market/releases/RELEASE_ID
sudo bash /opt/legendary-market/releases/RELEASE_ID/deploy/install-release.sh /opt/legendary-market/releases/RELEASE_ID
```

For first provision, create `/etc/legendary-market` mode 0755, store credential
environment file mode 0600, and copy config/ingestion.json there. Transfer credentials
over authenticated SSH stdin; never paste them into a command line or logs. Ensure
`python3-venv`, curl, Nginx, CA certificates and flock exist. Existing TLS certificate
renewal remains the operator's current setup.

To roll back code manually, point `current` at the previous known-good release and
restart both market services, then verify health/status/web. Schema1 is compatible
across this release. Restore a DB backup only with both market services stopped;
preserve the current database first and handle WAL sidecars together. Backups have
no automatic retention purge; schedule retention according to available disk space.

## CI/CD activation

`.github/workflows/market.yml` runs on PRs and pushes to main or codex branches.
It installs locked Python/npm dependencies, tests backend/frontend, typechecks,
builds and packages an allowlisted archive. The artifact excludes `.env`, private
keys, databases, node_modules and Git metadata. `release.json` records both commits.
Deploy jobs consume that exact build artifact rather than rebuilding on the server.
Release IDs include the commit, run ID and attempt number; reruns create distinct
directories and refuse to overwrite an existing release.

Create a GitHub `production` environment with appropriate protection. Set repository
or environment variables:

| Variable | Value |
| --- | --- |
| MARKET_DEPLOY_ENABLED | `true` when ready to enable main deployment |
| MARKET_DEPLOY_HOST | `13.184.199.172` |
| MARKET_DEPLOY_USER | SSH deployment user, currently `ubuntu` |

Set environment secrets `MARKET_DEPLOY_SSH_KEY` (a dedicated deployment private key)
and `MARKET_DEPLOY_KNOWN_HOSTS` (verified SSH host-key lines). Provision the matching
public key on the deployment account. That account must have sudo privileges for
the installer. Credentials and environment settings cannot be activated without a
GitHub account session; creating workflow YAML alone does not configure them.
Main pushes deploy only when the enable variable is true and verification passes.
Documentation-only pushes skip this workflow. The configured dedicated CI public
key uses OpenSSH `restrict` to disable PTY, forwarding and agent forwarding. It
still permits deployment commands; treat the production environment as privileged.

References: [FastAPI HTTPS proxy](https://fastapi.tiangolo.com/deployment/https/),
[Uvicorn proxy trust settings](https://uvicorn.dev/settings/), and
[GitHub secure use reference](https://docs.github.com/en/actions/reference/security/secure-use).
