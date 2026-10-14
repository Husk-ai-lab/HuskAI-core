# Data model

Oct 3, 2026 · @Marco

## Local SQLite (`husk.db`) — all product data

Defined in `app/gateways/sqlite/migrations/001_init.sql`; Pydantic models in `app/data/models/`.

| Table | Content | Retention |
| --- | --- | --- |
| `orgs`, `users`, `user_settings` | Local identity (email/name stay local), theme, personality, **language** (`en` default, `it`) | Until account removed |
| `sessions` | SHA-256 of UI session cookies | 30 days, never exported |
| `documents.language` | `it` or `en`, detected when the document is ingested (migration 010; NULL for older rows, detected on the fly). Document summaries are written in it | With the document |
| `connectors` | Status of the Telegram and Gmail connectors per org (`state`, a hint such as the bot's @name or the Gmail address, `last_error` as a short code; migration 009). **No credential**: those are in the OS keychain | Until removed in Settings; emptied in exports |
| ~~`user_ai_keys`~~ | Dropped in migration 008: no AI key is kept on this computer (AI goes through husk-gateway) | — |
| `documents`, `chunks`, `tabular_profiles` | Uploaded documents (metadata + converted markdown), chunks, sheet structure; `source_path` = relative path for folder uploads (migration 004, display only) | Until deleted by the user |
| `discovery_runs`, `discovery_steps`, `agent_checkpoints` | Runs, per-step outputs (incl. `usage`: calls and tokens per model), resume state; run errors stored as `error_key` + `error_params` (migration 003); `traced` (migration 005) | Kept as history |
| `doc_signals`, `opportunities` | Signal ranking and the opportunity map per run; opportunities carry the Explorer's `builder_prompt` | Kept with the run |
| `document_cards` | The summary of each document discovery explores first (migration 006): sentence, activities with evidence or computed spreadsheet facts; reused while `sha256` + card version are unchanged | Deleted with the document |
| `facts`, `fact_history` | Company profile (≤30) and replaced facts | Until reset |
| `networks`, `network_notes`, `memory_docs` | Knowledge networks, notes, versioned markdown artefacts | Kept networks live with their automation |
| `automations`, `automation_versions`, `automation_runs` | Graphs, every version, local run history | Until deleted |
| `chats`, `chat_messages`, `proposals` | Builder conversations and pending proposals. Messages sent from a suggestion carry `meta.origin = "suggestion"`; proposals carry `suggestion_id` | Until deleted |
| `suggestions` | What Husk noticed after documents changed (migration 004): kind, status (`new → seen → applied/dismissed`), automation, documents, title/body/prompt, checked evidence, confidence | Kept as history; exported with backups |
| `token_usage` | Tokens per org/user/day/model (local only) | Kept |
| `kv` | Cached maintenance/release status, last user | — |

Original files: `documents/<org>/<doc_id>.<ext>`. BM25 index: `bm25/<org>/` (rebuilt on import).

### Single-file backup

Settings → **Esporta** (`POST /api/v1/user/data/export`) writes `husk-export-<timestamp>.db` into **`<Documents>/Husk backups/`** (`HUSK_BACKUP_DIR` overrides; tests and servers use `<data>/backups`), shows the full path in a pop-up with a **Mostra nella cartella** button (`/data/export/reveal`, only names this app created), and never deletes anything there. The folder is outside the data folder on purpose: **Delete all data keeps it**. If it cannot be written, the backup goes to `<data>/exports/` and the pop-up says it would be removed by Delete all data. It is not a browser download, because the desktop shell's webview cannot save one (`GET /api/v1/user/data/export` stays for a plain browser). Safety copies made before an import (`exports/pre-import-*.db`, newest 3 kept) stay in the data folder. A failed export leaves no file. The file is:
**Importa** validates the file (integrity check, format, org, file hashes, safe paths), writes a safety copy (`exports/pre-import-*.db`), copies the snapshot into the live database with SQLite's online backup API, restores the files, migrates and rebuilds the indexes.

## Neon `husk-core` — anonymous, read-only Data API

Pydantic models: `app/data/neon/models.py`, one per table, matching the technical plan exactly (tested in `test/test_neon_maintenance.py`). SQL: `app/gateways/neon/husk_core_schema.sql`.

Husk holds **no Neon credential**. The Data API only accepts JWTs (a request with no token gets `400 missing authentication credentials`, verified on the real endpoint, 4 Oct 2026), so each GET carries a **short-lived anonymous token** from Neon Auth (`GET {NEON_AUTH_URL}/token/anonymous`: no sign-in, no secret, role claim `anonymous`). The token lives in memory only, is renewed 60 s before it expires or once after a 400/401, and is never stored or logged. The Data API runs the GET as the `anonymous` role. That role can read exactly two views and nothing else (also enforced by a test):

| View (exposed schema `public`) | Source | Husk uses it for |
| --- | --- | --- |
| `public.husk_maintenance` | `ops.maintenance` (current and upcoming windows) | **Maintenance switch**: polled at startup and every 60 s. A blocking window shows the full-screen notice and pauses AI and automation runs. Every upcoming window is announced: startup modal once per window (≤24 h ahead, also on the login screen), countdown banner in the last hour, warning before starting a discovery ≤30 min ahead, one desktop notification 10 min before a blocking window. Cached locally for offline starts |
| `public.husk_releases` | `ops.releases` | "Versione disponibile" notice |

Requests: `GET {NEON_DATA_API_URL}/husk_maintenance?select=…&ends_at=gt.<now>&order=starts_at.asc` and `GET {NEON_DATA_API_URL}/husk_releases?component=eq.husk`.

**Not done by Husk (needs a verified identity, so it belongs to husk-gateway):** reading `billing.billing_state`, and writing `core.organizations` / `core.users`. The other tables (`billing.stripe_events`, `usage.*`, `runtime.*`, `audit.log`) are modelled only.

Setup on Neon:
1. Enable the Data API on the branch and keep `anonymous` as the anonymous role (JWT role claim key `.role`).
2. Enable **Neon Auth** on the branch with **anonymous tokens on**.
3. Run the bottom section of `husk_core_schema.sql` (views + grants).
4. Put the REST endpoint in `NEON_DATA_API_URL` and the Neon Auth base URL in `NEON_AUTH_URL` (`app/config.py`). Until `NEON_AUTH_URL` is set the app runs with Neon checks off.

- [ ] Verify live, once Neon Auth is enabled: `GET {NEON_AUTH_URL}/token/anonymous` returns a token (the client reads `token`, `access_token` or `jwt`), and the two views answer 200 with it as the `anonymous` role.
- [ ] The schema has no pricing-plan column (only `status` and `installation_paid_at`); a plan tier needs a technical-plan change first.
