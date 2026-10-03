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
                 │  gateways/ sqlite (ALL product data) · zitadel · regolo · neon · studio │
                 └────┬──────────────┬──────────────┬───────────────┬────────────────────┘
                      │ OIDC+PKCE    │ HTTPS        │ HTTPS GET,     │ loopback only
                      │              │              │ anonymous      │
                   ZITADEL (EU)   Regolo.ai (IT)  Neon Data API    Husk Studio (optional, dev)
                                  zero retention  maint.+releases   OTLP + MCP
```

## Layers (repository map)

| Path | Responsibility |
| --- | --- |
| `app/main.py` | App factory, lifespan (migrations, maintenance poll, Studio probe, scheduler), localized error handlers, SPA serving |
| `app/config.py` | Public, committed configuration: ZITADEL issuer + client id, Regolo base URL + default model, Neon Data API URL, supported languages. Env vars override it |
| `app/i18n/` | Shared message catalogues (`locales/en.json`, `it.json`), `t()`, localized errors. See [I18N.md](I18N.md) |
| `app/endpoints/v1/` | HTTP API. `user/` (login, settings, backup), `documents/`, `discovery/`, `automations/`, `agent/` (builder chat), `suggestions/` (list, seen/dismiss/apply, SSE), `system.py` (health, status, Studio token) |
| `app/data/models/` | Pydantic domain models (documents, knowledge memory, discovery, automation graphs, agents) |
| `app/data/neon/` | Pydantic models 1:1 with the plan's `husk-core` schema (11 tables) |
| `app/gateways/` | Everything that talks to the outside: `sqlite/` (repositories, migrations, backup), `zitadel/`, `regolo/`, `neon/`, `studio/` |
| `app/core/knowledge/` | Conversion, chunking, BM25s index, search + highlights, `tabular/` data-scientist tools, `memory/` (the harness) |
| `app/core/agents/` | `harness/` (LangChain tool loop, structured outputs, personalities, tools, self-heal), `explorer/` (discovery), `builder/` (automation editing), `orchestrator.py` (Husk Op) |
| `app/core/automations/` | Graph executor, decision provider, local actions, scheduler, use cases |
| `app/core/suggestions/` | Debounced suggestion jobs after uploads/deletes (see [HARNESS.md](HARNESS.md#suggestions-after-documents-change)) |
| `app/helpers/` | Settings, OS data paths, PII-scrubbing logging, ids, time, text |
| `test/` | pytest suite (scripted LLM in `test/fakes.py`) |
| `benchmarks/` | Synthetic company + benchmarks + value simulations for investors |
| `installer.py` | PyInstaller one-folder build + smoke test |

## Configuration

| Kind | Where | Committed |
| --- | --- | --- |
| Public identifiers (ZITADEL issuer/client id, Regolo URL + default model, Neon Data API URL, languages) | `app/config.py` | Yes |
| Overrides for dev/staging, developer AI key | `.env` (root) | No |
| Values passed by the future shell (`HUSK_PORT`, `HUSK_DATA_DIR`, `HUSK_LAUNCH_SECRET`, `HUSK_STUDIO_*`) | Environment | — |
| Per-user AI key | `user_ai_keys` in the local SQLite | No (and never exported) |

## Where data lives

**All product data is local**, in the OS application-data folder (`HUSK_DATA_DIR` overrides it, as the future shell will):

| OS | Folder |
| --- | --- |
| macOS | `~/Library/Application Support/ai.husk.desktop/data/husk` |
| Windows | `%APPDATA%\ai.husk.desktop\data\husk` |
| Linux | `~/.local/share/ai.husk.desktop/data/husk` |

Contents: `husk.db` (SQLite, WAL), `documents/<org>/` (original files), `bm25/<org>/` (index, rebuildable), `outputs/<org>/` (automation outputs), `exports/` (backups), `logs/`.

**Neon (`husk-core`) is read anonymously and read-only** through the Neon Data API: two public views, the maintenance switch and release notices. No credential and no Authorization header; nothing is written. See [DATA_MODEL.md](DATA_MODEL.md).

## Multi-tenancy (B2B)

* Every product table carries `org_id`, taken from the ZITADEL `resourceowner` claim. Repositories are constructed with the caller's org, so endpoint code cannot forget the filter.
* Roles come from ZITADEL project roles (`owner`/`admin`/`member`).
* Several orgs can share one installation (e.g. a consultant machine); backups refuse to import into a different org.

## Security on the machine

* Bind to `127.0.0.1` only. State-changing requests must come from an allowed `Origin` (`LocalGuard`), so a website cannot drive the backend.
* `/internal/*` requires the shell's per-launch secret (`HUSK_LAUNCH_SECRET`).
* UI session: random cookie, only its SHA-256 stored. Refresh token in the OS keychain (`keyring`), access token in memory.
* Logs never contain document text or personal data (`helpers/logging.scrub` as a last line of defence).

## Contracts kept for the next phases

| Contract | Where | Why |
| --- | --- | --- |
| Env vars `HUSK_PORT`, `HUSK_DATA_DIR`, `HUSK_LAUNCH_SECRET`, `HUSK_STUDIO_*` | `helpers/settings.py` | The Tauri shell passes them (plan → *Opening the app*) |
| `/health`, `/auth/callback`, `/internal/studio-token` | `endpoints/v1/system.py`, `user/router.py` | Shell health check, ZITADEL redirect, Studio token |
| `LLMProvider` | `gateways/regolo/provider.py` | Swap direct Regolo for `husk-gateway` without touching agents |
| `DecisionProvider` | `core/automations/decision.py` | Serenity replaces LLM decisions behind the same interface |
| `NeonGateway` | `gateways/neon/gateway.py` | Swap the anonymous Data API for the gateway's public `/v1/status` with no other change |
| MCP tool discovery | `gateways/studio/client.py` | Uses only tools Studio advertises (`list_tools()`) |

## Deviations from the technical plan (temporary, MVP)

| Plan rule | MVP | Way back |
| --- | --- | --- |
| Rule 3 — no paid secret on the customer's computer | Regolo key provided by Husk AI, entered by the user after login, verified with Regolo, stored in local `user_ai_keys` (`.env` = developer fallback) | Point `LLMProvider` at `husk-gateway`; drop `user_ai_keys` |
| Husk never talks to Neon directly | Anonymous, read-only Neon Data API for two public views (maintenance, releases). **No credential on the machine** | Point `NeonGateway` at `/v1/status` |
| Paywall (rule 6) | Not implemented: billing state needs a verified identity, so it waits for husk-gateway | Gateway `/v1/entitlement` |
| Tecnico / Non tecnico profiles | One view for everyone | Re-introduce with a setting |
| Fly.io 24/7 runtime | "Attiva" = local scheduler only | Deploy endpoint in the gateway |
| Manual automations / user AI agents | Not available; edits only through the builder | Later phase |
| Serenity | LLM structured decisions | `DecisionProvider` |
| Product copy "nessun dato viene conservato" (GDPR G3) | Replaced with the precise statement from the GDPR guide | — (G3 closed in this repo) |
