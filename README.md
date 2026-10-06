# Husk

**Automation discovery and automations for Italian manufacturing SMEs.** Upload company documents; Husk finds the repetitive work hidden in them, measures it in hours, and turns it into automations you edit by talking to an assistant. Everything runs on your computer.

> MVP product repository (Oct 2026). Source-available under the Business Source License 1.1. Company context: `Company_memory/` (private).

## What's inside

- **Login** with your company account (Clerk): the sign-in page opens in your browser and Husk continues by itself. No guest mode, no sign-up.
- **AI through the Husk gateway**: no AI key on your computer; your login is the credential, the gateway checks the installation is active.
- **Connectors** (Telegram, Gmail): automations send messages, emails and PDF reports. A first-login popup and a Settings section guide the setup; a connector that stops working becomes a suggestion.
- **Maintenance alerts**: scheduled maintenance announced at startup (once per window), countdown in the last hour, desktop notification shortly before.
- **Conoscenza**: PDF, DOCX, XLSX, CSV, PPTX, Markdown and SQLite files are converted locally and indexed with BM25s. Spreadsheets are navigated as tables with exact numbers.
- **Discovery** in 7 visible, resumable steps: signal ranking → deep research into knowledge networks → handoffs between departments → impact in hours → a map of automations.
- **Harness memory**: a ≤30-fact company profile, plus one knowledge network and summary per automation, all in markdown.
- **Husk Assistant**: edits automations in plain language through typed, validated operations, with personalities (Balanced, Ultra-careful, Explanatory, Concise) and undo.
- **English and Italian**: language on the login screen and changeable in Settings; agents write in the user's language. One shared catalogue in `app/i18n`.
- **Prova / Carica / Attiva**: test runs and scheduled runs on this computer only.
- **Backup**: export everything to one `.db` file and import it back.
- **Husk Studio compatible**: OpenTelemetry traces + MCP self-heal when a local Studio is running, plus a "Visualizza in Husk Studio" link when something fails.
- Light and dark theme.

## Quick start

```bash
uv sync
# public config (Clerk, gateway URL, models, Neon Data API) is already in app/config.py
cp .env.example .env            # optional: local overrides
cd app/frontend && npm install && npm run build && cd ../..
uv run python -m app.main       # → http://127.0.0.1:8765
```

Without `uv`: `pip install -r requirements.txt` (exported from `uv.lock`).

Tests: `uv run pytest` · Benchmarks: `uv run python -m benchmarks.report` · Desktop binary: `uv run python installer.py --smoke`

## Docs

| Doc | |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, data locations, multi-tenancy, contracts, deviations from the plan |
| [docs/HARNESS.md](docs/HARNESS.md) | Agents, discovery steps, memory rules, retrieval order |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | SQLite tables, backup format, Neon usage |
| [docs/STUDIO_COMPATIBILITY.md](docs/STUDIO_COMPATIBILITY.md) | OTLP, MCP, self-heal, deep links |
| [docs/CLERK_SETUP.md](docs/CLERK_SETUP.md) | Login (Clerk) and gateway configuration, what to check in the dashboard |
| [docs/CONNECTORS.md](docs/CONNECTORS.md) | Telegram and Gmail connectors: setup, security, how automations and the Builder use them |
| [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) | Tokens, patterns, differences from the prototype |
| [docs/BENCHMARKS.md](docs/BENCHMARKS.md) | Investor benchmarks and honesty rules |
| [docs/I18N.md](docs/I18N.md) | Languages, message keys, how to add a language |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | Latest test run, coverage, what is not tested |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Setup, run, test, package |

## Privacy

Your documents stay on this computer. AI requests go to Regolo.ai (Italy), which does not retain them. The app holds no database credential: it only reads the public maintenance and release notices.
