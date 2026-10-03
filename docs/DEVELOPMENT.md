# Development

Oct 3, 2026 · @Marco

## Prerequisites

Python 3.11–3.13 with **uv**, Node.js 20+.

```bash
uv sync                               # backend deps (dev group included)
cp .env.example .env                  # optional: overrides of app/config.py, dev REGOLO_API_KEY
cd app/frontend && npm install && cd ../..
```

`requirements.txt` / `requirements-dev.txt` are exported from `uv.lock` for pip-based environments:
`uv export --no-hashes --no-dev --format requirements-txt -o requirements.txt`.

## Run

```bash
# production-like: build the UI once, FastAPI serves it
cd app/frontend && npm run build && cd ../..
uv run python -m app.main             # http://127.0.0.1:8765

# UI development with hot reload
uv run python -m app.main             # terminal 1 (HUSK_FRONTEND_DEV_URL=http://localhost:5173 in .env)
cd app/frontend && npm run dev        # terminal 2 → http://localhost:5173 (proxies /api and /auth)
```

## Test

```bash
uv run pytest                         # backend: unit + API + discovery e2e (scripted LLM, no network)
cd app/frontend && npm test           # UI
uv run ruff check app test benchmarks
```

`test/fakes.py` contains a scripted LangChain chat model: it exercises structured outputs, tool loops and note-validation retries deterministically.

## Package

```bash
uv run python installer.py --smoke    # UI build + PyInstaller one-folder (dist/husk-backend/) + /health check
```

The `.env` file is never bundled: for a local test of the binary, put a `.env` next to `dist/husk-backend/husk-backend` (the shell will pass env vars in production). Signing/notarisation run in CI.

## Conventions

* Docs in English; user-facing text via `app/i18n` (EN default + IT), see [I18N.md](I18N.md).
* Public identifiers in `app/config.py` (committed); secrets only in `.env`.
* Every product row carries `org_id`. Use the repositories, never raw SQL in endpoints.
* No personal data or document text in logs. Fixtures are synthetic.
* New Neon columns or tables need a technical-plan change first.
* Automations change only through `builder/graph_ops.py` operations.
