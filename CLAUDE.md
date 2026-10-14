# CLAUDE.md — Husk (MVP product repository)

Read this first. Company context lives in `Company_memory/` (read `Company_memory/AGENTS.md`). The architecture source of truth is `Company_memory/HuskAI/md/Technical plan for Husk AI.md`. This repo's own docs are in `docs/`.

## What this repo is

The plain **Husk** product: FastAPI + React (Vite) on `127.0.0.1` (a native webview or a browser), Clerk login (login only, system browser + PKCE), all AI through **husk-gateway**, local knowledge base (MarkItDown + BM25s), discovery with internal LangChain agents (Husk Op → Explorer + Builder), automations edited **only** through the Builder and run **only locally**; they can send messages, emails and reports through **connectors** (Telegram, Gmail). Out of scope: the Tauri shell (it never logs in), Husk Studio, the gateway's own code (this app is only its client), Serenity API, Stripe, Fly.io.

## Map

- `app/main.py` app factory + `--data-dir` (server guard: `app/helpers/runtime.py`) · `app/config.py` public config · `app/i18n` messages (en/it) · `app/endpoints/v1/{user,documents,discovery,automations,agent,suggestions}` API
- `app/data/models` domain models · `app/data/neon` husk-core models (exact plan schema)
- `app/gateways/{sqlite,clerk,gateway,connectors,decisions,neon,studio}` external I/O (`clerk/`: login + tokens; `gateway/`: husk-gateway client, LLM provider, decision adapter; `connectors/`: Telegram + Gmail; `decisions/`: the decision-model interface and the questions discovery asks)
- `app/core/connectors` (connector service: status, verify, send) · `app/core/knowledge` (BM25, `tabular/` data tools, `memory/` harness) · `app/core/agents/{harness,explorer,builder}` + `orchestrator.py` · `app/core/automations` · `app/core/suggestions` (after uploads/deletes)
- `app/core/shell_bridge.py` requests Husk makes to the desktop shell (the shell pulls `/internal/shell/requests`) · `app/core/metering.py` tokens per step/model · `app/frontend` React UI · `test/` pytest · `benchmarks/` (`bench_discovery_tokens.py`: live/offline discovery benchmark) · `installer.py` · `scripts/release.py` (package, report, one-machine build) · `.github/workflows/desktop-build.yml` · `docs/` (`docs/agents/` agent architecture)

## Hard rules

1. **All product data stays local (SQLite + files).** Neon is reached ONLY through the anonymous Data API: read-only GETs of `public.husk_maintenance` and `public.husk_releases` with a short-lived anonymous Neon Auth token (the Data API rejects requests without a JWT): no stored credential, no user JWT. Never add a Neon credential to the app; billing state and identity writes belong to husk-gateway. Never add Neon columns not in the technical plan.
2. **No code coupling with Husk Studio.** Talk only via OTLP and advertised MCP tools. `test_no_code_coupling_with_studio` must stay green. Studio MCP tools reach the Builder only through the read-only allowlist and only when useful (`harness/studio_tools.py`); self-heal messages always end in a proposal. Errors store `trace_id` + `traced`, never a link.
3. **Every product row is `org_id`-scoped.** Go through the repositories in `gateways/sqlite/repositories.py`.
4. **Login only.** No guest mode, no sign-up routes (tested).
5. **Automations change only through `core/agents/builder/graph_ops.py`** and are validated by `AutomationGraph`. No manual creation and no manual structure editing. The one thing a user sets by hand is how a **notification** node delivers (channel, recipients, subject, message, report): `AutomationService.edit_notification`, one `update_node` op through `graph_ops`, saved as a new version (Undo works), applied at once.
6. **Harness invariants are code, not prompts:** company profile ≤ 30 facts (side facts replace weaker ones only when judged better); notes always complete, validated, rewritten whole, and backed by evidence (a passage + quote, or a computed spreadsheet fact from the document card). Discovery explores document cards first and goes deeper only where confidence is low: see `docs/agents/EXPLORER_AGENT.md`.
7. **GDPR:** no personal data or document text in logs; synthetic fixtures only; UI copy must stay accurate (GDPR guide G3).
8. Docs in English. **All user-facing text goes through `app/i18n`** (EN default + IT): `t()` / `api_error()` / `LocalizedError` in Python, `useT()` in React. Add every key to both `locales/en.json` and `it.json`; tests and the no-hard-coded-text guard enforce it. See `docs/I18N.md`.
9. **Public config** (Clerk issuer + client id, husk-gateway URL, default models, Neon Data API URL, languages) lives in the committed `app/config.py`. **Secrets only in `.env`** (git-ignored); never bundle `.env` in builds. The app itself needs no secret at all.
10. **No AI key on the machine.** Every AI call goes through husk-gateway (`gateways/gateway/`) with the signed-in user's Clerk access token as Bearer; the gateway holds the Regolo.ai and Venice keys, checks that the organisation has paid and meters usage. The sign-in refresh token lives in a private owner-only file in the data folder (`session-secrets.json`, `HUSK_SECRET_STORE=file`, the alpha default for the same reason as connector secrets: an unsigned build loses the keychain item after every rebuild and the user is signed out) or, with `HUSK_SECRET_STORE=keychain`, in the OS keychain (`gateways/clerk/tokens.py`); access tokens in memory: never in SQLite, logs or backups; Delete all data keeps it (the user stays signed in), sign-out removes and revokes it. Every AI path uses `LLMProvider` / `DecisionModel` (a direct provider exists only in `benchmarks/direct.py`). **Connector secrets** (Telegram bot token, Gmail App Password) live in a private owner-only file in the data folder (`connector-secrets.json`, plain text, `HUSK_SECRET_STORE=file`, the alpha default because an unsigned macOS build is asked for the keychain password again after every rebuild) or, with `HUSK_SECRET_STORE=keychain`, in the OS keychain; either way they survive sign-out, are removable in Settings, and never appear in SQLite, API responses, logs or backups (the file store is removed by Delete all data); connector calls never log or store an address, a chat id, a message or an exception text. Everything that tells someone something is ONE node kind, **notification** (channel `desktop` | `telegram` | `gmail`, one per node); a notification that reaches people outside this computer (Telegram, Gmail) added or changed by the **Builder** is **always** a proposal, enforced in code (`changes_sends`). A test run ("Prova") only ever reaches the user themselves; **Run now** is the explicit, confirmed real run.
11. **Suggestions never change anything by themselves.** Modify suggestions go through the Builder chat as *suggestion messages* (stricter `SUGGESTION_RULES`, tagged, always a proposal). Evidence is validated in code. See `docs/HARNESS.md`.
12. **Discovery decisions go through the decision-model interface** (`gateways/decisions/base.py`, served by husk-gateway `/v1/decide` via `gateways/gateway/decisions.py`): links, relation types, repetitiveness, hours and depth are typed decisions with gates (uncertain = no), never LLM text. The LLM only writes cards, briefs and drafts. Max 50 documents per organisation (hard cap). See `docs/agents/DECISION_MODEL.md`.

## Commands

```bash
uv sync && (cd app/frontend && npm install)
uv run pytest                       # must pass before any commit
(cd app/frontend && npm run build && npm test)
uv run python -m app.main           # http://127.0.0.1:8765
uv run python -m app.main --data-dir /var/lib/husk   # REQUIRED on a server/container (auto-detected, exit 2 without it)
uv run python -m benchmarks.report  # offline benchmarks
uv run python installer.py --smoke  # binary + /health
uv run python scripts/release.py build --targets mac-arm64  # archive + report on this machine (docs/RELEASING.md)
```

## Standing instructions from the CTO (permanent)

1. **Change reporting.** After creating or modifying files, structures or services, report in chat, scaled to the change: small edit → 2–4 lines; new file/module → what, why, how it connects; new service/structural change → purpose, architecture impact, dependencies, files, follow-ups. No filler.
2. **Research awareness.** When touching AI/ML code (retrieval, agents, RAG, embeddings), flag genuinely applicable recent techniques briefly. Only when they really apply.
3. **Augment, don't overhaul.** Don't restructure working core logic unless asked. Extend, wrap or add adjacent modules. Bug fixes in core are allowed but scoped to the fix. If a task seems to need an overhaul, stop and flag it.
