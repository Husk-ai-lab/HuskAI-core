# Architecture — Husk MVP

Oct 3, 2026 · @Marco (CTO) · source of truth for the product architecture: `Company_memory/HuskAI/md/Technical plan for Husk AI.md`

Husk is **one FastAPI process** on `127.0.0.1` that serves its own React UI, owns the user's login, and does all document work locally. This repository is the plain product (the plan's `husk` repo) **without** the desktop shell, Husk Studio, the gateway or the Serenity API.

```text
                 ┌──────────────────────── customer's computer ─────────────────────────┐
  browser/webview│  React UI (Vite build, served by FastAPI)                             │
        ───────▶ │  endpoints/v1: user · documents · discovery · automations · agent     │
                 │     │                                                                 │
                 │  core/ ── knowledge (MarkItDown → chunks → BM25s, data tools, memory)  │
                 │        ── agents/orchestrator (Husk Op) → explorer · builder (LangChain)│
                 │        ── automations (graph executor, local scheduler)                │
                 │     │                                                                 │
                 │  gateways/ sqlite (ALL product data) · clerk · gateway · connectors · neon · studio │
                 └────┬──────────────┬──────────────┬───────────────┬────────────────────┘
                      │ OAuth+PKCE   │ HTTPS+Bearer │ HTTPS GET,     │ loopback only
                      │              │              │ anonymous      │
                    Clerk (login)  husk-gateway   Neon Data API    Husk Studio (optional, dev)
                                  → Regolo / Venice  maint.+releases   OTLP + MCP
                   + Telegram / Gmail, only for what the user's automations send (connectors)
```

## Layers (repository map)

| Path | Responsibility |
| --- | --- |
| `app/main.py` | App factory, lifespan (migrations, maintenance poll, Studio probe, scheduler), localized error handlers, SPA serving |
| `app/config.py` | Public, committed configuration: Clerk issuer + client id, husk-gateway URL, default models, Neon Data API URL, supported languages. Env vars override it |
| `app/i18n/` | Shared message catalogues (`locales/en.json`, `it.json`), `t()`, localized errors. See [I18N.md](I18N.md) |
| `app/endpoints/v1/` | HTTP API. `user/` (login, settings, backup), `documents/`, `discovery/`, `automations/`, `agent/` (builder chat), `suggestions/` (list, seen/dismiss/apply, SSE), `system.py` (health, status, Studio token) |
| `app/data/models/` | Pydantic domain models (documents, knowledge memory, discovery, automation graphs, agents) |
| `app/data/neon/` | Pydantic models 1:1 with the plan's `husk-core` schema (11 tables) |
| `app/gateways/` | Everything that talks to the outside: `sqlite/` (repositories, migrations, backup), `clerk/` (OAuth + PKCE login, tokens), `gateway/` (husk-gateway client: Bearer + rate gate, LLM provider, `/v1/decide` adapter, entitlement), `connectors/` (Telegram, Gmail, keychain secrets), `neon/`, `studio/` |
| `app/core/knowledge/` | Conversion, chunking, BM25s index, search + highlights, `tabular/` data-scientist tools and `facts.py` (spreadsheet cards), `memory/` (the harness) |
| `app/core/agents/` | `harness/` (LangChain tool loop, structured outputs, personalities, tools, self-heal, Studio MCP tools), `explorer/` (discovery: `summarizer.py` cards, confidence-driven exploration — [agents/EXPLORER_AGENT.md](agents/EXPLORER_AGENT.md)), `builder/` (automation editing), `orchestrator.py` (Husk Op) |
| `app/core/metering.py` | Token metering per step and per model, budget guard |
| `app/core/automations/` | Graph executor, decision provider, local actions, scheduler, use cases |
| `app/core/suggestions/` | Debounced suggestion jobs after uploads/deletes (see [HARNESS.md](HARNESS.md#suggestions-after-documents-change)) |
| `app/helpers/` | Settings, OS data paths, PII-scrubbing logging, ids, time, text |
| `test/` | pytest suite (scripted LLM in `test/fakes.py`) |
| `benchmarks/` | Synthetic company + benchmarks + value simulations for investors |
| `installer.py` | PyInstaller one-folder build + smoke test |

## Configuration

| Kind | Where | Committed |
| --- | --- | --- |
| Public identifiers (Clerk issuer/client id, gateway URL, default models, Neon Data API URL, languages) | `app/config.py` | Yes |
| Overrides for dev/staging; provider keys for benchmarks only | `.env` (root) | No |
| Values passed by the future shell (`HUSK_PORT`, `HUSK_DATA_DIR`, `HUSK_LAUNCH_SECRET`, `HUSK_STUDIO_*`) | Environment | — |
| Clerk refresh token | `session-secrets.json` in the data folder, 0600 (default, `HUSK_SECRET_STORE=file`) or the OS keychain (`keychain`); never SQLite | No (and never exported); kept by Delete all data, removed on sign-out |
| Connector credentials (Telegram bot token + chat, Gmail App Password) | `connector-secrets.json` in the data folder, 0600, plain text (default, `HUSK_SECRET_STORE=file`) or the OS keychain (`HUSK_SECRET_STORE=keychain`); never SQLite | No (never in a backup export) |

## Where data lives

**All product data is local**, in the OS application-data folder (`HUSK_DATA_DIR` overrides it, as the future shell will):

| OS | Folder |
| --- | --- |
| macOS | `~/Library/Application Support/ai.husk.desktop/data/husk` |
| Windows | `%APPDATA%\ai.husk.desktop\data\husk` |
| Linux | `~/.local/share/ai.husk.desktop/data/husk` |

Contents: `husk.db` (SQLite, WAL), `documents/<org>/` (original files), `bm25/<org>/` (index, rebuildable), `outputs/<org>/` (automation outputs), `exports/` (safety copies before an import, and the fallback for backups), `logs/`. Exported backups live in `<Documents>/Husk backups/` (`HUSK_BACKUP_DIR`), outside this folder.

**Delete all data** (Settings → Delete all data, `DELETE /api/v1/user/data`, `gateways/sqlite/wipe.py`): removes only those Husk-made files and folders (never the data root itself, which may be a shared folder), empties the logs, recreates an empty migrated database and keeps the user signed in with their settings. `connector-secrets.json` (file store) is removed with them. Keychain items (the login, and connector secrets when `HUSK_SECRET_STORE=keychain`) are not in the folder and stay.

**Server mode.** `helpers/runtime.py` tells a desktop from a server (`HUSK_SERVER=1`, containers, hosting-platform variables, headless Linux). On a server `python -m app.main` **requires `--data-dir PATH`** (absolute and writable) and exits with code 2 without it; `HUSK_DATA_DIR` alone is not accepted there, and `resolve_data_paths` never falls back to the OS folder. This protects the data folder only: a real server deployment still needs a non-loopback login redirect (it is `http://127.0.0.1:{port}/callback`), a secret store without a keychain (secrets are otherwise kept in memory), a configurable host and origins for `LocalGuard`.

**Neon (`husk-core`) is read anonymously and read-only** through the Neon Data API: two public views, the maintenance switch and release notices. No credential: each call carries a short-lived anonymous token from Neon Auth, held in memory only; nothing is written. See [DATA_MODEL.md](DATA_MODEL.md).

## Multi-tenancy (B2B)

* Every product table carries `org_id`, taken from the `org_id` claim of the Clerk access token (a user without an organisation cannot sign in: the gateway would answer `401 organisation claim missing`). Repositories are constructed with the caller's org, so endpoint code cannot forget the filter.
* Clerk does not put an organisation name or roles in the token: the UI shows the user's email, and every user is a `member` for now.
* Several orgs can share one installation (e.g. a consultant machine); backups refuse to import into a different org.

## Security on the machine

* Bind to `127.0.0.1` only. State-changing requests must come from an allowed `Origin` (`LocalGuard`), so a website cannot drive the backend.
* `/internal/*` requires the shell's per-launch secret (`HUSK_LAUNCH_SECRET`).
* UI session: random cookie, only its SHA-256 stored. Refresh token in a private file or the OS keychain (`HUSK_SECRET_STORE`), access token in memory.
* Logs never contain document text or personal data (`helpers/logging.scrub` as a last line of defence).

## Contracts kept for the next phases

| Contract | Where | Why |
| --- | --- | --- |
| Env vars `HUSK_PORT`, `HUSK_DATA_DIR`, `HUSK_LAUNCH_SECRET`, `HUSK_STUDIO_*` | `helpers/settings.py` | The Tauri shell passes them (plan → *Opening the app*) |
| `/health`, `/callback`, `/auth/done`, `/internal/studio-token` | `endpoints/v1/system.py`, `user/router.py`, UI route | Shell health check, Clerk redirect (`http://127.0.0.1:8765/callback`), the "you can close this tab" page, Studio token |
| `LLMProvider` / `DecisionModel` | `gateways/gateway/llm.py`, `gateways/decisions/base.py` | Agents never see a provider: today the gateway, tomorrow any other |
| `DecisionProvider` | `core/automations/decision.py` | Serenity replaces LLM decisions behind the same interface |
| `NeonGateway` | `gateways/neon/gateway.py` | Swap the anonymous Data API for the gateway's public `/v1/status` with no other change |
| MCP tool discovery | `gateways/studio/client.py` | Uses only tools Studio advertises (`list_tools()`) |

## Deviations from the technical plan (temporary, MVP)

| Plan rule | MVP | Way back |
| --- | --- | --- |
| Rule 3 — no paid secret on the customer's computer | **Met**: all AI goes through `husk-gateway` with the user's Clerk token; no AI key on the machine. (`user_ai_keys` was dropped in migration 008.) | — |
| Husk never talks to Neon directly | Anonymous, read-only Neon Data API for two public views (maintenance, releases), with a short-lived anonymous Neon Auth token. **No stored credential on the machine** | Point `NeonGateway` at `/v1/status` |
| Paywall (rule 6) | The gateway decides (`/v1/entitlement`, billing state); the app shows an "installation not active" screen when it says no and keeps the last answer for offline opening. The 72-hour signed entitlement token of the plan is not implemented | Gateway-issued token |
| Customer data stays local | Except what the user's own automations send through the connectors they set up (Telegram, Gmail): that goes from this computer straight to Telegram / Google | Document in the GDPR guide data map |
| Tecnico / Non tecnico profiles | One view for everyone | Re-introduce with a setting |
| Fly.io 24/7 runtime | "Attiva" = local scheduler only | Deploy endpoint in the gateway |
| Manual automations / user AI agents | Not available; edits only through the builder | Later phase |
| Serenity | LLM structured decisions | `DecisionProvider` |
| Product copy "nessun dato viene conservato" (GDPR G3) | Replaced with the precise statement from the GDPR guide | — (G3 closed in this repo) |
