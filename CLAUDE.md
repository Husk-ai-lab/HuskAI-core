# CLAUDE.md — Husk (MVP product repository)

Read this first. Company context lives in `Company_memory/` (read `Company_memory/AGENTS.md`). The architecture source of truth is `Company_memory/HuskAI/md/Technical plan for Husk AI.md`. This repo's own docs are in `docs/`.

## What this repo is

The plain **Husk** product: FastAPI + React (Vite) on `127.0.0.1`, ZITADEL login (login only), local knowledge base (MarkItDown + BM25s), discovery with internal LangChain agents (Husk Op → Explorer + Builder), automations edited **only** through the Builder and run **only locally**. Out of scope: Tauri shell, Husk Studio, husk-gateway, Serenity API, Stripe, Fly.io.

## Map

- `app/main.py` app factory · `app/config.py` public config · `app/i18n` messages (en/it) · `app/endpoints/v1/{user,documents,discovery,automations,agent,suggestions}` API
- `app/data/models` domain models · `app/data/neon` husk-core models (exact plan schema)
- `app/gateways/{sqlite,zitadel,regolo,decisions,neon,studio}` external I/O (`decisions/`: the decision model, Venice Jev now)
- `app/core/knowledge` (BM25, `tabular/` data tools, `memory/` harness) · `app/core/agents/{harness,explorer,builder}` + `orchestrator.py` · `app/core/automations` · `app/core/suggestions` (after uploads/deletes)
- `app/core/metering.py` tokens per step/model · `app/frontend` React UI · `test/` pytest · `benchmarks/` (`bench_discovery_tokens.py`: live/offline discovery benchmark) · `installer.py` · `docs/` (`docs/agents/` agent architecture)

## Hard rules

1. **All product data stays local (SQLite + files).** Neon is reached ONLY through the anonymous Data API: read-only GETs of `public.husk_maintenance` and `public.husk_releases` with a short-lived anonymous Neon Auth token (the Data API rejects requests without a JWT): no stored credential, no user JWT. Never add a Neon credential to the app; billing state and identity writes belong to husk-gateway. Never add Neon columns not in the technical plan.
2. **No code coupling with Husk Studio.** Talk only via OTLP and advertised MCP tools. `test_no_code_coupling_with_studio` must stay green. Studio MCP tools reach the Builder only through the read-only allowlist and only when useful (`harness/studio_tools.py`); self-heal messages always end in a proposal. Errors store `trace_id` + `traced`, never a link.
3. **Every product row is `org_id`-scoped.** Go through the repositories in `gateways/sqlite/repositories.py`.
4. **Login only.** No guest mode, no sign-up routes (tested).
5. **Automations change only through `core/agents/builder/graph_ops.py`** and are validated by `AutomationGraph`. No manual creation in this phase.
6. **Harness invariants are code, not prompts:** company profile ≤ 30 facts (side facts replace weaker ones only when judged better); notes always complete, validated, rewritten whole, and backed by evidence (a passage + quote, or a computed spreadsheet fact from the document card). Discovery explores document cards first and goes deeper only where confidence is low: see `docs/agents/EXPLORER_AGENT.md`.
7. **GDPR:** no personal data or document text in logs; synthetic fixtures only; UI copy must stay accurate (GDPR guide G3).
8. Docs in English. **All user-facing text goes through `app/i18n`** (EN default + IT): `t()` / `api_error()` / `LocalizedError` in Python, `useT()` in React. Add every key to both `locales/en.json` and `it.json`; tests and the no-hard-coded-text guard enforce it. See `docs/I18N.md`.
9. **Public config** (ZITADEL issuer/client id, Regolo base URL + default model, Neon Data API URL, languages) lives in the committed `app/config.py`. **Secrets only in `.env`** (git-ignored); never bundle `.env` in builds.
10. **AI key** (simplified BYOK): ONE string = Regolo key + Venice key (`…VENICE_INFERENCE_KEY_…`), split by regex; saved per user in `user_ai_keys` ONLY after Regolo.ai AND Venice AI accept their part; **deleted on sign-out**; changeable/removable in Settings; never returned by the API (hint only), never logged, always stripped from backups. Every AI path must use the caller's key (`LLMProvider.key_for(user_id)`).
11. **Suggestions never change anything by themselves.** Modify suggestions go through the Builder chat as *suggestion messages* (stricter `SUGGESTION_RULES`, tagged, always a proposal). Evidence is validated in code. See `docs/HARNESS.md`.
12. **Discovery decisions go through the decision-model interface** (`gateways/decisions/base.py`): links, relation types, repetitiveness, hours and depth are typed decisions with gates (uncertain = no), never LLM text. The LLM only writes cards, briefs and drafts. Max 50 documents per organisation (hard cap). See `docs/agents/DECISION_MODEL.md`.

## Commands

```bash
uv sync && (cd app/frontend && npm install)
uv run pytest                       # must pass before any commit
(cd app/frontend && npm run build && npm test)
uv run python -m app.main           # http://127.0.0.1:8765
uv run python -m benchmarks.report  # offline benchmarks
uv run python installer.py --smoke  # binary + /health
```

## Standing instructions from the CTO (permanent)

1. **Change reporting.** After creating or modifying files, structures or services, report in chat, scaled to the change: small edit → 2–4 lines; new file/module → what, why, how it connects; new service/structural change → purpose, architecture impact, dependencies, files, follow-ups. No filler.
2. **Research awareness.** When touching AI/ML code (retrieval, agents, RAG, embeddings), flag genuinely applicable recent techniques briefly. Only when they really apply.
3. **Augment, don't overhaul.** Don't restructure working core logic unless asked. Extend, wrap or add adjacent modules. Bug fixes in core are allowed but scoped to the fix. If a task seems to need an overhaul, stop and flag it.
