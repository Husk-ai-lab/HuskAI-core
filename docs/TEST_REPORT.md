# Test report — Husk MVP

Oct 3, 2026 · @Marco · run after adding Suggestions (light bulb page, snackbars, suggestion messages) and folder upload

## Summary

| Suite | Result | Time |
| --- | --- | --- |
| Backend — pytest (`test/`) | **101 passed**, 0 failed, 0 skipped (warnings as errors) | 11.7 s |
| Backend line coverage (`app/`) | **88%** (5,949 statements, 737 missed); `core/suggestions/engine.py` 90%, `endpoints/v1/suggestions` 80%, `app/i18n` 95–100% | — |
| Frontend — TypeScript (`tsc -b`) | clean | — |
| Frontend — vitest | **49 passed** (incl. the per-file no-hard-coded-text guard over every `.tsx` file) | 0.8 s |
| Lint — ruff (`app test benchmarks installer.py`) | all checks passed | — |
| Benchmarks — offline (`python -m benchmarks.report`) | all ran; results below | ~10 s |
| Desktop binary — `installer.py --smoke` | built (187 MB, one-folder), `/health` → `{"status":"ok"}` | ~3 min |
| Visual check | Prototype screens light + dark; AI-key screen, maintenance modal/banner; login, key screen, discovery report and editor in **English and Italian**; light bulb with dot, Suggestions page (EN light, IT dark), editor opened from a suggestion, suggestion bubble + proposal (headless Chrome, scripted model, Valsorda documents uploaded as a folder) | manual |

**Not covered by automated tests:** the real Regolo.ai model, the real ZITADEL login, the real Neon Data API, a running Husk Studio, and Windows/Linux builds. See [What is not tested](#what-is-not-tested).

Environment: macOS 26.6 arm64 · Python 3.11.15 (uv) · Node 22 · FastAPI 0.142 · LangChain-core 1.6 · BM25s 0.3.12 · PyInstaller 6.22 · pytest 9.1.

## How to reproduce

```bash
uv run pytest                                              # backend
uv run --with pytest-cov pytest --cov=app --cov-report=term # with coverage
(cd app/frontend && npx tsc -b && npm test)                # frontend
uv run ruff check app test benchmarks installer.py         # lint
uv run python -m benchmarks.report                         # offline benchmarks → benchmarks/results/REPORT.md
uv run python installer.py --skip-ui --smoke               # binary + /health
```

No test needs network access or secrets. External services are replaced by:
- `test/fakes.py`: a **scripted LangChain chat model**. It returns structured outputs and tool calls, including a deliberately truncated note that the validator must reject before a corrected rewrite.
- `respx`: mocks ZITADEL discovery and token endpoints, the Neon Data API and Husk Studio.
- In-memory token storage instead of the OS keychain.

Each test gets its own temporary data folder and SQLite database.

## What each test file verifies

### `test_suggestions.py` — suggestions after document changes (14)
| Test | Verifies |
| --- | --- |
| `test_before_discovery_only_suggests_running_it` | Before any discovery: one `run_discovery` suggestion, deduplicated, no model call |
| `test_new_document_suggests_change_with_checked_evidence` | New document after discovery → `modify_automation` on the right automation; evidence only from that document; invented-evidence and low-confidence drafts dropped; seen clears the dot; re-uploading creates no duplicate |
| `test_profile_change_creates_memory_update` | A new company fact → one `memory_update` listing it; "Got it" marks it applied |
| `test_suggestion_message_always_becomes_a_proposal` | With the auto-applying personality, a suggestion message still ends in a **proposal**; the prompt carries `SUGGESTION_RULES` and the "NOT written by the user" tag (also on history replay); accepting → applied and the document joins the automation's notes |
| `test_edited_suggestion_is_the_users_own_request` | Edited text loses the suggestion origin and follows the normal personality |
| `test_discarding_the_proposal_dismisses_the_suggestion` | Discard → suggestion dismissed |
| `test_suggestion_of_another_automation_is_refused` | Unknown/foreign `suggestion_id` → 404 |
| `test_deleting_a_used_document_suggests_a_review` | Deleting a cited document → `source_deleted` with the file name in the prompt; `/apply` refuses (changes go through the chat); dismiss works |
| `test_new_automation_suggestion_creates_a_draft` | `new_automation` → draft with summary, `run_id = None`; second apply → 409 |
| `test_upload_batches_are_debounced_into_one_job` | 3 uploads → 1 job with 3 documents |
| `test_no_ai_key_means_no_analysis` | No key → no model call, no suggestion |
| `test_suggestions_are_org_scoped` | Another org sees nothing and gets 404 |
| `test_folder_upload_keeps_safe_relative_paths` | `paths` stored as `source_path`; `../` removed; bad filter → 400 |
| `test_documents_covered_by_discovery_are_not_reanalysed` | A late upload job finds the documents discovery just analysed and does nothing |

Frontend `suggestions.test.tsx` (8): bulb dot only with unread; snackbar on an SSE `created` event with a "View" action, gone after 6 s; Suggestions page shows the right actions per kind and marks new ones as seen; chat pre-fills a suggestion, marks it, and switches to a user request when edited; stored suggestion messages render differently; folder filter (system/hidden/unsupported files) and recursive drop walker.


### `test_discovery_e2e.py` — full discovery through the API (3)
| Test | Verifies |
| --- | --- |
| `test_discovery_end_to_end` | Upload of 3 synthetic documents (MD, XLSX, CSV) → run → all **7 steps done** → report: documents, opportunities, activities, hours > 0 → automations created as *bozza* with trigger/agent/condition/action nodes → ≤30 facts → **one kept network + summary per automation** → state badge *fatta* → documents show "used by" |
| `test_discovery_requires_documents` | Starting without documents → 400 |
| `test_discovery_resume_after_failure` | The model fails at step 6 → run *failed* with steps 1–5 done → resume → *completed*, with **fewer than 10 new model calls** (finished steps are not paid for again) |

### `test_builder_chat.py` — Assistente Husk (3)
| Test | Verifies |
| --- | --- |
| `test_chat_edits_flow_and_versions` | Chat over SSE adds a node through a typed operation → automation **version 2** → *undo* restores version 1 |
| `test_ultra_careful_personality_proposes` | Ultra-prudente → a **proposal**, not a change; version unchanged until accepted; applying it updates the node; it can't be applied twice |
| `test_personalities_listed` | Exactly the 4 personalities |

### `test_automations.py` — graph model, operations, executor (7)
| Test | Verifies |
| --- | --- |
| `test_compile_and_layout` | Draft → graph; left-to-right layout; "vero" branch above "falso" |
| `test_graph_validation_rules` | Rejects unlabelled condition branches, loops, two triggers |
| `test_operations` | add / remove (reconnects the flow) / set_trigger; invalid branch and trigger removal are refused |
| `test_condition_evaluation` | Numeric, string and dotted-path conditions |
| `test_executor_prova_runs_locally` | Prova follows only the taken branch; email is **simulated** |
| `test_executor_writes_pdf_on_false_branch` | The other branch writes a real PDF report |
| `test_status_and_test_run_api` | Prova API, run history, *Attiva* schedules a next run locally, *Carica* unschedules |

### `test_memory.py` — the harness (5)
| Test | Verifies |
| --- | --- |
| `test_fact_cap_and_side_fact` | 30 facts max; a weak side fact is rejected, a strong one **replaces** the weakest; history logged; markdown shows 30/30 |
| `test_duplicate_fact_reinforces` | A near-duplicate reinforces the existing fact instead of adding one |
| `test_custom_llm_judge_is_used` | A pluggable judge decides replacements |
| `test_notes_must_be_complete` | Rejects truncated claims, missing evidence and unknown documents; drops dangling links; rewrites are whole and versioned |
| `test_keep_one_network_per_automation` | Clone notes into the kept network, discard the others; builder context order: profile → summary → notes |

### `test_knowledge.py` — document pipeline (7)
| Test | Verifies |
| --- | --- |
| `test_tokens_keep_codes_and_stem_italian` | Product codes stay exact; Italian stemming |
| `test_chunker_keeps_table_header` | Split tables repeat the header row in every chunk |
| `test_upload_requires_authorisation` | No authorisation → 400 |
| `test_search_and_preview` | BM25 search with correct highlight offsets; sheet profile (rows, columns) |
| `test_duplicate_upload_is_deduplicated` | Same file content uploaded twice → one document |
| `test_tabular_tools` | Missing values, BOM rev B vs C diff, daily periodicity, aggregate, filter |
| `test_sqlite_db_document` | `.db` files ingested and queried as tables |

### `test_i18n.py` — languages and public config (14)
| Test | Verifies |
| --- | --- |
| `test_supported_languages_have_catalogues` | Every language in `app/config.py` has a catalogue; default is `en` |
| `test_same_keys_and_placeholders_as_english[it]` | Italian has exactly the English keys, the same `{placeholders}`, no empty values |
| `test_plural_pairs_are_complete` | Every `_one` has an `_other` |
| `test_every_key_used_in_code_exists` | Static scan of Python and TSX: every key used is defined |
| `test_dynamic_key_families_are_complete` | Keys built at runtime exist for every enum value (steps, statuses, priorities, states, fact categories, note kinds, personalities) |
| `test_translate_fallback_plural_and_params` | Unsupported language → English; unknown key → key; plurals; language-dict params |
| `test_language_resolution` | `Accept-Language` parsing and normalisation |
| `test_api_errors_follow_accept_language` | Same `code`, `detail` in English vs Italian per request header |
| `test_personalities_and_steps_localized` | Personality labels follow the request language |
| `test_default_user_language_is_english` | New users default to English |
| `test_agents_write_in_the_users_language` | The explorer's system prompt tells the model "in Italian" vs "in English" according to the user setting |
| `test_public_config_defaults_and_env_override` | Defaults come from `app/config.py`; env vars and explicit overrides win |
| `test_config_holds_no_secrets` | No variable in `config.py` is named like a credential |
| `test_model_id_matching` | "gpt oss 120B" matches Regolo's `openai/gpt-oss-120b`; fallback to the preference list |

### `test_ai_key.py` — AI key after login (10)
| Test | Verifies |
| --- | --- |
| `test_no_key_blocks_ai_but_not_local_work` | No key → AI endpoints 503 "Chiave AI mancante"; local work still works |
| `test_valid_key_is_verified_then_saved_in_user_data` | Key checked against Regolo `/models` with the right header, then saved in `user_ai_keys` (user, org, hint, model); never echoed back |
| `test_rejected_key_is_not_saved` | Regolo 401 → 400, nothing stored, the setup screen stays |
| `test_unverifiable_key_is_not_saved` | Network down → 503, nothing stored (verification is mandatory) |
| `test_key_without_models_is_refused` | A key with no models → refused |
| `test_input_validation_and_delete` | Too short → 422; contains spaces → 400; delete works |
| `test_keys_are_per_user` | Each user's AI calls use their own key; a user without a key is refused |
| `test_key_never_exported_but_survives_import` | The key is absent from the backup file; the importing user keeps their key |
| `test_env_key_is_a_developer_fallback` | `.env` key used only when the user has none |
| `test_mask` | Only the last 4 characters are ever shown |

### `test_neon_maintenance.py` — Neon via the anonymous Data API, maintenance alerts (18)
| Test | Verifies |
| --- | --- |
| `test_blocking_window_blocks` | An active blocking window blocks; release notice computed |
| `test_non_blocking_and_future_windows` | Non-blocking → banner only; announced future window → not active |
| `test_window_ends_on_time_without_a_new_poll` | Blocking stops at `ends_at` even between polls |
| `test_all_upcoming_windows_listed_soonest_first` | Every scheduled window returned, soonest first, also from the cache after a restart |
| `test_desktop_notification_once_before_blocking_window` | One notification 10 min before a blocking window; none for far or non-blocking windows; not repeated after restart |
| `test_desktop_notification_in_users_language` | The notification text and message follow the user's language |
| `test_system_status_exposes_upcoming_before_login` | Upcoming windows visible without a session (login screen alert) |
| `test_offline_uses_cache` | Neon unreachable after a restart → cached blocking status still applies |
| `test_not_configured_never_blocks` | No `NEON_DATA_API_URL` → never blocks |
| `test_data_api_is_anonymous_and_read_only` | GET only, **no Authorization header**, correct PostgREST filters |
| `test_data_api_releases` | Release rows parsed; `component=eq.husk` |
| `test_data_api_errors_fall_back_to_cache` | HTTP 503 from the Data API → cached status |
| `test_data_api_requires_https_and_build` | Plain `http://` refused; the right gateway is chosen from settings |
| `test_maintenance_blocks_ai_endpoints` | During maintenance discovery returns 503 with the Italian message; documents still work |
| `test_me_exposes_no_billing` | `/me` returns only user + settings |
| `test_no_database_credential_anywhere` | No `asyncpg`, DSN, Authorization header, billing/core/usage access or INSERT in the Neon gateway code; no `NEON_DATABASE_URL` in the app |
| `test_anonymous_grants_are_only_the_two_views` | The SQL grants `anonymous` exactly: USAGE on `public`, SELECT on the two views |
| `test_neon_models_match_plan_schema` | The 11 Pydantic models match the technical plan's columns exactly |

### `test_auth.py` — ZITADEL, login only (6)
| Test | Verifies |
| --- | --- |
| `test_pkce_pair` | S256 challenge matches the verifier |
| `test_identity_from_claims` | User, org and roles from the claims; a token without an org is refused |
| `test_protected_routes_require_login` | 401 on product routes; `/health` and system status stay public |
| `test_login_flow` | Login URL (PKCE, login hint, redirect, scopes) → callback → session → `/me` → state is single-use → logout → 401 |
| `test_no_guest_or_signup_routes` | The API has no guest, ospite, signup or register routes |
| `test_settings_roundtrip` | Theme and personality saved |

### `test_backup.py` — single-file export/import (4)
| Test | Verifies |
| --- | --- |
| `test_export_contains_everything` | Metadata, 3 original files as BLOBs, all product tables filled; **sessions stripped** |
| `test_import_roundtrip` | Delete everything → import → documents, files and search restored; the user stays signed in |
| `test_import_requires_confirmation_and_valid_file` | No confirmation → 400; invalid file → 400 |
| `test_import_rejects_other_org` | A backup from another org is refused |

### `test_studio.py` — Husk Studio compatibility (5)
| Test | Verifies |
| --- | --- |
| `test_no_code_coupling_with_studio` | No Studio package is imported anywhere in `app/` |
| `test_probe_when_studio_absent` | Studio absent → not alive, no Studio link |
| `test_probe_when_studio_alive` | Alive + OTLP + MCP tools detected; run deep link built |
| `test_non_loopback_studio_is_refused` | A Studio on another machine is ignored |
| `test_self_heal_retries_with_diagnosis` | Failure → Studio diagnosis → one retry with the hint |

### `test_security_paths.py` — local hardening (5)
| Test | Verifies |
| --- | --- |
| `test_foreign_origin_cannot_write` | A foreign Origin gets 403; the app's own origin is allowed |
| `test_internal_requires_launch_secret` | `/internal/*` without the launch secret → 403 |
| `test_data_dir_env_override` / `test_default_data_dir_is_os_app_data` | `HUSK_DATA_DIR` override; default is the OS app-data folder |
| `test_logs_scrub_personal_data` | Email, phone and codice fiscale are scrubbed from logs |

### Frontend — `app/frontend/src/test/` (38)
- `ui.test.tsx` (3): login has no guest option · the Studio link appears only when a URL exists · agent markdown is rendered safely.
- `i18n.test.tsx` (29): catalogue parity · `translate()` interpolation, plurals and fallback · browser-language detection and saved choice · **no hard-coded UI text**: parses every `.tsx` (26 files, one test each) with the TypeScript compiler and fails on JSX text or `placeholder`/`title`/`aria-label`/`alt` literals with letters.
- `maintenance.test.tsx` (6): the AI-key screen switches the whole UI between English and Italian and remembers the choice (+ the 5 below).
- `maintenance.test.tsx` (5): schedule helpers (past windows filtered, next blocking window) · acknowledgements remembered · the startup modal shows once and stays dismissed · windows more than 24 h away are ignored and the English message is used · the AI key screen requires a key, masks it and offers logout.

## Coverage: where it is thin

| Module | Cover | Why / what's missing |
| --- | --- | --- |
| `gateways/regolo/provider.py` | 74% | Key lookup and validation are tested; the real chat client is never called (the scripted model replaces it). Needs a live smoke test |
| `core/automations/actions.py` | 51% | Desktop notifications, Excel output and writing to a custom folder are untested |
| `gateways/studio/client.py` | 53% | Real MCP sessions are mocked out |
| `core/events.py` | 54% | SSE back-pressure and the event replay buffer are untested |
| `endpoints/v1/system.py` | 54% | `/internal/studio-token` success path (needs a launch secret and a refresh token) |
| `gateways/zitadel/token_store.py` | 57% | The OS keychain path is disabled in tests |
| `core/automations/scheduler.py` | 64% | Real cron firing and folder watching are not exercised |

## Offline benchmarks (same run)

| Measure | Result |
| --- | --- |
| BM25s query latency p50 / p95 (109 chunks) | 2.8 ms / 4.0 ms |
| Retrieval recall@3 / MRR (8 labelled queries, 2 corpus sizes) | 1.0 / 0.94–1.0 |
| Company profile: mean importance of the 30 kept facts from 300 candidates | 0.943 (ideal 0.945, random 0.511) |
| Note validator precision / recall | 1.0 / 1.0 (25 bad, 20 good) |
| Heuristic signal ranking precision@k | 1.0; noise documents ranked last |
| Data tools exact answers | 8 / 8 |
| Discovery pipeline (scripted model) | completed, 15 documents, 0.5 s; hours not reported offline (honesty rule) |

AI-quality numbers (hours discovered against the ~702 h/year of planted repetitive work) need `--live` with a Regolo key. They have **not** been run yet.

## Bugs found by testing and fixed

| Found by | Bug | Fix |
| --- | --- | --- |
| `test_discovery_end_to_end` | Starting a discovery from a sync endpoint had no event loop (`create_task` failed) | Task-spawning endpoints are now `async` |
| `test_tabular_tools` | `dayfirst=True` read ISO dates `2026-09-01` as 9 January, so a daily report looked monthly | ISO dates are detected and parsed year-first |
| Visual check | The editor canvas was empty because React Flow needs a parent with a height | Canvas container takes the full height |
| Visual check | Sidebar items overflowed and their icons collapsed; every document looked selected | Truncation fixed; active state now follows the selected document |
| Binary build | `--collect-all mcp` pulled in `mcp.cli`, which needs `typer` | Collect only data-carrying packages; exclude `mcp.cli` |
| Code review (AI key) | Backup import read the user's key *after* the database was replaced, so the key would have been lost | The key is read before the import and restored afterwards (`test_key_never_exported_but_survives_import`) |
| Visual check | The countdown banner made the page taller than the window and cut off the sidebar | The shell is clipped to the viewport; content scrolls inside |
| Visual check | The maintenance date was Title-Cased ("Dalle … Alle") | Only the first letter is capitalised |
| Provider refactor | A text splice duplicated half of `regolo/provider.py` (end marker matched an earlier `chat_model`) | File rebuilt; caught immediately by the suite |
| Text guard (TS AST) | The editor breadcrumb still hard-coded `automazioni/` | Uses `automations.crumb` |
| Visual check (IT) | "Vedi tutti" used for automations (feminine in Italian) | Separate key `nav.see_all_automations` → "Vedi tutte" |
| Code review | Builder/explorer prompts, tool and validator messages were Italian | Instructions in English + `agent.write_in`; documents keep their language |
| `test_no_database_credential_anywhere` | The first version flagged words inside the module docstring | The test now inspects code only (docstrings and comments stripped) |
| `test_import_roundtrip` (after Suggestions) | Import refused (409) whenever *any* background task was pending, so a queued suggestion job blocked restores | Import blocks only on a running discovery and cancels queued suggestion jobs |
| Visual check (Suggestions) | The upload job of the documents discovery had just analysed ran late and suggested changes for all of them | Jobs wait while a discovery runs and skip documents of the latest completed run (`test_documents_covered_by_discovery_are_not_reanalysed`) |
| Visual check (Suggestions) | Fallback quotes from spreadsheets showed raw markdown table rows | Table rules and pipes are turned into a readable `a · b · c` line |

## What is not tested

- [ ] **Live Regolo model**: discovery quality, real tool-calling and structured outputs on the chosen model (`benchmarks --live`), and a real key being accepted or refused by `/models`
- [ ] **Real model following the language instruction** (Italian vs English output from gpt-oss-120b on Regolo)
- [ ] **Desktop notification** on Windows (only macOS `osascript` and Linux `notify-send` are wired)
- [ ] **Real ZITADEL**: hosted login, refresh, logout redirect, project roles in tokens, sign-up disabled on the instance
- [ ] **Real Neon Data API**: requests **without** an Authorization header served as `anonymous` (the docs disagree), PostgREST filters on the views, and that only the two views are readable
- [ ] **Running Husk Studio**: OTLP spans arriving, MCP tool names, the run deep-link route
- [ ] **Real scheduling**: a cron trigger firing and a watched folder reacting while Husk runs
- [ ] **UI end-to-end**: browser tests (e.g. Playwright) for upload → discovery → chat. Currently only a manual visual check
- [ ] **Windows and Linux** binaries and keychains; antivirus behaviour of the PyInstaller build
- [ ] **Load**: large document sets (thousands of files) and long chat histories
