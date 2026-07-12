# e2e-ai monitor UI (source)

Material UI (React + Vite) source for the `e2e-ai ui` dashboard.

**Node is only needed to build this UI, never to run e2e-ai.** Installed users
get the pre-built assets bundled in the wheel.

## Develop

```bash
make monitor-ui-install     # npm install (in monitor-ui/)
# In another terminal, run the API server:
e2e-ai ui --port 8765
# Then start the Vite dev server (proxies /api -> :8765):
cd monitor-ui && npm run dev
```

## Build (ship the assets)

```bash
make monitor-ui-build
```

`vite build` writes the production bundle straight into
`../src/e2e_ai/monitor/static/`, which the Python wheel ships. Commit the
regenerated `static/` assets so `pip install "e2e-ai[monitor]"` continues to work
without Node.

> The `static/index.html` currently committed is a dependency-free interim UI so
> the monitor works before this Vite/MUI source is built. Building this project
> replaces it with the Material UI bundle.
