# Verification evidence — 2026-11-16

## Before deployment

- Full Python unittest suite: **45 passed**, including the existing data, feature
  and prediction-model tests and new storage/API/worker tests.
- Frontend Vitest suite: **7 passed**; TypeScript typecheck and Vite production build passed.
- npm audit including development dependencies: **0 vulnerabilities** at verification time.
- Pinned Python environment: `pip check` reports no broken requirements. PyArrow
  remains 25.0.1 to preserve compatibility with the existing Streamlit environment.
- Real Alpaca SIP preflight: **8,785 bars**, 7 configured symbols, full retained
  history, completed sessions only, zero provider error; isolated local SQLite DB.
- Built-app HTTP smoke: 2 static assets, 4 populated ETF proxy cards, 42 AAPL sessions
  in the recent 60-day range, conditional GET 304, invalid range 422, write method 405.
- Browser visual inspection: **unverified**. Computer Use stopped because its policy
  check could not confidently determine Chrome's current URL. No screenshot or
  responsive visual verification is claimed; automated UI behavior/build tests passed.

## Production baseline

- Existing welcome page returns HTTPS 200.
- Discord bot PID 532; existing unit SHA256
  `b26c91f5c4c0dbdeb72560a44132c96113ce934a1b31f0d1331ef4aa8339f804`.
- Welcome page SHA256
  `9c25dfdcffe9f15d72856704e04f24185f580201eff1935ecd42fe8e9c73bf5e`.
- Existing Nginx syntax check passed before changes.

## GitHub verification

Ubuntu/Python 3.12/Node 22 workflow [44002739494](https://github.com/strangers11250/LegendaryBroker/actions/runs/44002739494)
passed backend tests, frontend checks, built-app HTTP smoke and artifact packaging
for backend `4a62875` / frontend `3eed81d`. Frontend repository is public, allowing pinned
submodule checkout without an additional repository credential.

Production environment, dedicated restricted CI SSH identity, verified host-key
secret and deployment variables are configured. The CI deployment receives no
provider credentials; its credential copy exists only on the server.

The first production attempt (run 44003329085) passed verification, scratch-DB smoke,
live ingestion and localhost API readiness. Its immediate HTTPS check received 404
during graceful Nginx reload. Automatic rollback restored original routing and stopped
only market services. Discord PID 532, its unit file and welcome-page checksum were
unchanged. The installer now waits for new HTTPS routing and runs the full HTTP smoke
before declaring success; initial rollback also removes enable symlinks before units.

## Successful production rollout

Run [44004457112](https://github.com/strangers11250/LegendaryBroker/actions/runs/44004457112)
completed verification and deployment successfully for backend `35b503d` / frontend `3eed81d`.
The server published `/web/` and `/api/` after a new successful Alpaca refresh.
Independent HTTPS checks from the development computer also passed:

- Built HTML and both linked assets return 200, four proxy cards have stored prices,
  and AAPL returns 42 recent sessions. ETag conditional requests return 304; invalid
  ranges return 422 and POST requests return 405.
- Production status: 8,785 rows, 7 symbols, SIP, all-adjusted, 1827-day retained window,
  interval 6 hours, no error or missing symbols, latest stored session 2026-11-13.
  Latest success observed at 2026-11-17T02:39:34Z (November 16 Pacific time).
- Both market services are active with zero restarts. Measured memory was about
  63 MiB for API and 54 MiB for ingestion; the host retained about 389 MiB available.
- State directory mode 0750, SQLite mode 0640, credential environment mode 0600.
- Nginx syntax check passed. Original welcome-page and Discord unit checksums match
  the baseline above; Discord's PID remained 532 and it stayed active throughout.
- Production environment is restricted to main, deployment is enabled, and frontend
  revisions are pinned. Secrets are provisioned; activation is complete.

The final workflow change adds unique run-attempt suffixes and refuses existing
release directories. Its branch verification run 44004627480 passed. Production
run [44004810358](https://github.com/strangers11250/LegendaryBroker/actions/runs/44004810358)
then passed both verification and deployment for backend `307f247` / frontend `3eed81d`.
The active release is
`/opt/legendary-market/releases/307f24770de2f25511a52de2f1dac9ec0ef73cb8-44004810358-1`.
Its required fresh fetch completed at 2026-11-17T02:42:40Z with 8,785 bars and no
provider error. Both market services stayed active with zero restarts; Nginx syntax,
Discord PID 532 and both original-feature checksums were checked again and matched.
The production branch policy allows main only, and both CI deployment secrets are
present. The temporary local copy of the CI private key was removed after verification.
