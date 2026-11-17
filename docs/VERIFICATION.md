# Verification evidence — 2026-11-16

## Before deployment

- Full Python unittest suite: **45 passed**, including the existing data, feature
  and prediction-model tests and new storage/API/worker tests.
- Frontend Vitest suite: **7 passed**; TypeScript typecheck and Vite production build passed.
- npm audit including development dependencies: **0 vulnerabilities** at verification time.
- Pinned Python environment: `pip check` reports no broken requirements. PyArrow
  remains25.0.1 to preserve compatibility with the existing Streamlit environment.
- Real Alpaca SIP preflight: **8,785 bars**,7configured symbols, full retained
  history, completed sessions only, zero provider error; isolated local SQLite DB.
- Built-app HTTP smoke:2static assets,4populated ETF proxy cards,42AAPL sessions
  in the recent60-day range, conditional GET304, invalid range422, write method405.
- Browser visual inspection: **unverified**. Computer Use stopped because its policy
  check could not confidently determine Chrome's current URL. No screenshot or
  responsive visual verification is claimed; automated UI behavior/build tests passed.

## Production baseline

- Existing welcome page returns HTTPS200.
- Discord bot PID532; existing unit SHA256
  `b26c91f5c4c0dbdeb72560a44132c96113ce934a1b31f0d1331ef4aa8339f804`.
- Welcome page SHA256
  `9c25dfdcffe9f15d72856704e04f24185f580201eff1935ecd42fe8e9c73bf5e`.
- Existing Nginx syntax check passed before changes.

## GitHub verification

Ubuntu/Python3.12/Node22 workflow [44002739494](https://github.com/strangers11250/LegendaryBroker/actions/runs/44002739494)
passed backend tests, frontend checks, built-app HTTP smoke and artifact packaging
for backend4a62875/frontend3eed81d. Frontend repository is public, allowing pinned
submodule checkout without an additional repository credential.

Production environment, dedicated restricted CI SSH identity, verified host-key
secret and deployment variables are configured. Provider credentials exist only on
the server. Production rollout results are appended after they are observed.
