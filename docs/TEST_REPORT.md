# Test report — Husk MVP

Oct 6, 2026 · @Marco · run after the pivot to Clerk login, husk-gateway (no AI key on the machine) and the Telegram / Gmail connectors

## Summary

| Suite | Result | Time |
| --- | --- | --- |
| Backend — pytest (`test/`) | **205 passed**, 0 failed, 0 skipped | ~25 s |
| Backend line coverage (`app/`) | 89% at the last measurement (Oct 4, before Neon token, progress, Clerk, gateway and connectors; `coverage` is not installed here, so not re-measured) | — |
| Frontend — TypeScript (`tsc -b`) | clean | — |
| Frontend — vitest | **100 passed** (incl. the discovery counter and its single event stream, the notification node and popup, chat markdown, login polling, the "you can close this tab" page, the first-login connectors popup, the guides in both languages, and the per-file no-hard-coded-text guard over every `.tsx` file) | 1.2 s |
| Lint — ruff (`app test benchmarks installer.py`) | all checks passed | — |
| Benchmarks — offline (`python -m benchmarks.report`) | all ran; results below | ~10 s |
| Desktop binary — `installer.py --smoke` | built (one-folder), `/health` → `{"status":"ok","version":"0.1.0"}` (Oct 6) | ~3 min |
| Discovery benchmark — live (`bench_discovery_tokens --live`, Valsorda 30 docs) | completed in **29 s**, 75 calls, $0.014; 6/9 planted tasks; see `benchmarks/results/DISCOVERY_BENCHMARK.md` | 29 s |
| Visual check | (Oct 4, before the pivot and before the notification node, popup and chat markdown; the login, popup and Settings screens of this change have NOT been looked at in a browser) Prototype screens light + dark; maintenance modal/banner; login, key screen, discovery report and editor in **English and Italian**; light bulb with dot, Suggestions page (EN light, IT dark), editor opened from a suggestion, suggestion bubble + proposal (headless Chrome, scripted model, Valsorda documents uploaded as a folder) | manual |

**Not covered by automated tests:** how the accent notification node, its popup and the formatted chat look in a real browser, the live discovery counter on 30 documents, document-language summaries from the real model, the real Clerk login, the real husk-gateway (chat, decisions, entitlement), real Telegram and Gmail, the real Neon Data API, a running Husk Studio, and Windows/Linux builds. See [What is not tested](#what-is-not-tested).

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
- `respx`: mocks Clerk (discovery, JWKS, token, userinfo, revoke) with a locally generated RSA key, husk-gateway, the Telegram Bot API, the Neon Data API and Husk Studio. Gmail is tested with a fake `smtplib.SMTP_SSL`.
- In-memory token storage instead of the OS keychain.

Each test gets its own temporary data folder and SQLite database.

## What each test file verifies

New in this round: `test_jev_discovery.py` (Jev request shape vs the documented API, batching, 402, metering; combined key split and two-part verification; candidate-pair cap; edge gating and components; assessment and BM25 scoping to flagged documents; full discovery on the decision model; Venice key required), `test_document_cards.py` (cards, cache, fallback, concurrency), `test_fast_discovery.py` (loop repair, failed draft skipped, reasoning policy low/medium/never high, summary deadline, progress reported as counters), `test_i18n.py` (discovery copy never sells a "free" pre-purchase map), `discovery.test.tsx` (4 grouped stages, one calm status line), `test_token_metering.py` (per step/model, budget guard, offline benchmark), `test_upload_limit.py` (50-document cap).


### `test_studio_chat.py` — Husk Studio in the product (10)
| Test | Verifies |
| --- | --- |
| `test_status_exposes_install_url_and_template` | `/system/status` carries the install URL and the run-link template |
| `test_only_read_only_tools_are_kept` | Advertised write tools (`delete_run`, `clear_all`) are never offered and `call_tool` refuses them |
| `test_studio_tools_wrap_mcp_calls` | Read-only MCP tools become `studio_*` LangChain tools calling Studio; none without Studio; failure keywords (en/it) |
| `test_studio_tools_are_bound_only_for_failures` | Normal edits don't bind Studio tools; questions about errors do; never without MCP |
| `test_last_failed_run_is_in_context` | The last failed run (trace, step, error) is in the Builder context and binds Studio tools |
| `test_self_heal_message_reads_studio_and_proposes` | A self-heal message calls `explain_failure` with the trace, carries `SELF_HEAL_RULES` + tag, and ends in a proposal even with the auto-applying personality |
| `test_self_heal_requires_a_real_trace_id` | A non-hex trace id → 422 |
| `test_crashed_chat_request_retries_with_studio_diagnosis` | A crashed chat request retries once with Studio's diagnosis in the prompt |
| `test_chat_errors_store_the_trace_not_a_link` | Chat errors store `trace_id` + `traced`, no link |
| `test_runs_record_whether_they_were_traced` | Test runs record `traced` |

Frontend `studio.test.tsx` (9): run/home/install targets; StudioAction install vs run link; Diagnose only with MCP; sidebar card (install/open, collapsible, never hidden); Suggestions advice card hidden for 30 days; chat self-heal pre-fill and bubble; error bubbles always offer a Studio action.


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
| `test_not_signed_in_to_the_gateway_means_no_analysis` | No gateway sign-in → no model call, no suggestion |
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
| `test_executor_prova_runs_locally` | Prova follows only the taken branch; the email step is **skipped** because Gmail is not set up (the run goes on) |
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

### `test_i18n.py` — languages and public config (16)
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

### `test_gateway.py` — husk-gateway client, provider, decision adapter (22)
| Test | Verifies |
| --- | --- |
| `test_requests_carry_the_bearer_token_and_a_user_agent` | `Authorization: Bearer <Clerk token>` and the explicit `User-Agent` (Cloudflare) on every call |
| `test_an_invalid_token_is_renewed_once` | 401 "invalid token" → one refresh through Clerk and one retry with the new token |
| `test_no_organisation_is_not_retried_and_is_explained` | 401 "organisation claim missing" is not retried and maps to `errors.gateway_no_org` |
| `test_gateway_answers_become_localized_errors` (4) | 403 / 429 / 503 / 400 → not entitled / busy / unavailable / rejected |
| `test_without_a_sign_in_…` · `test_unreachable_gateway_…` | No token → "sign in again" and nothing is sent; network down → a clean error |
| `test_rate_gate_keeps_us_under_the_limit` | The 4th request in a minute waits for the window (fake clock) |
| `test_chat_goes_through_the_gateway_with_the_users_token` | `ChatOpenAI` over the gateway: model, `reasoning_effort`, `max_tokens`, usage recorded locally and in the meter |
| `test_not_entitled_stops_at_once_…` | 403 is not retried, is mapped, and neither the token nor the prompt is logged |
| `test_no_sign_in_no_model` | No usable token → `LLMNotConfigured` |
| `test_yes_no_question_…` · `test_choice_maps_…` · `test_ten_band_score_…` · `test_eight_hour_buckets_…` | The `/v1/decide` adapter: `noul`, `choice`, 10-band `score`, 8-bucket hours; probabilities keyed by level index |
| `test_state_is_trimmed_to_the_gateway_limit` | 12 000-character state limit: short lines kept, long summaries shortened evenly |
| `test_decide_batches_by_16_and_decodes` · `test_decide_errors_are_localized` | 16 questions per request, request shape, metering, error mapping |
| `test_entitlement_is_cached_…` · `test_not_paid_is_reported_…` | `/api/v1/user/entitlement`: cached for offline, unknown never blocks, an expired gateway session ends the local one |

### `test_connectors.py` — Telegram and Gmail (17)
| Test | Verifies |
| --- | --- |
| `test_long_messages_are_split_at_line_breaks` · `test_telegram_detects_the_latest_chat_…` · `test_telegram_paces_splits_retries_once_…` | 4096-character split; chat detection and the webhook-active case; one message per second, `retry_after` honoured once, PDF sent |
| `test_telegram_errors_are_codes_and_never_carry_the_token` | Failures are short codes, no exception chain, the bot token is not in any log |
| `test_recipients_are_addresses_only` · `test_gmail_login_errors_are_codes` | Recipient parsing; 535 / data error / network → `auth_failed` / `failed` / `unreachable` |
| `test_nothing_is_saved_unless_the_provider_accepts` | Verify-before-save for both; invalid input and missing chat refused |
| `test_secrets_live_in_the_keychain_only` | No token, password or chat id in SQLite, logs or an exported backup; the connectors table is emptied in exports; remove deletes the credential |
| `test_status_survives_an_imported_backup_…` | A credential without a status row reads as ready; a status row without a credential reads as not set up |
| `test_api_never_returns_a_secret` · `test_api_reports_why_a_connector_is_refused` | API flow (detect chat, save, test, remove) and localized reasons |
| `test_runs_send_log_no_content_and_prova_stays_with_the_user` | Real runs send; the run log has no body or address; Prova → own address only, `[Test]`/`[Prova]` subject |
| `test_a_connector_that_is_not_set_up_skips_…` · `test_a_failed_send_fails_the_run_…` | Not set up → step skipped, run goes on; a role is not an address; a revoked App Password fails the run without leaking and marks the connector `error` |
| `test_builder_is_told_which_connectors_work` · `test_a_change_to_a_sending_step_is_detected` | The connector block in the Builder context; `changes_sends` / `kinds_used` |
| `test_a_dead_connector_becomes_one_suggestion_…` | One suggestion per connector, closed when it is set up, raised again when a run fails |

### `test_neon_maintenance.py` — Neon via the anonymous Data API, maintenance alerts (21)
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
| `test_data_api_sends_the_anonymous_token_and_is_read_only` | GET only, Bearer = the anonymous Neon Auth token (the token call itself has no credential), correct PostgREST filters |
| `test_anonymous_token_is_cached_then_refreshed_before_expiry` | One token fetch for many calls; renewed inside the 60 s margin |
| `test_rejected_token_is_renewed_once` | A 400 renews the token and retries once; a second 400 surfaces |
| `test_token_is_never_persisted_or_logged` | The token is absent from logs and SQLite |
| `test_data_api_releases` | Release rows parsed; `component=eq.husk` |
| `test_data_api_errors_fall_back_to_cache_and_warn_once` | HTTP 503 → cached status; one warning, not one per poll |
| `test_data_api_requires_https_and_both_urls` | Plain `http://` refused; the gateway needs both URLs, else Neon checks are off |
| `test_maintenance_blocks_ai_endpoints` | During maintenance discovery returns 503 with the Italian message; documents still work |
| `test_me_exposes_no_billing` | `/me` returns only user + settings |
| `test_no_database_credential_anywhere` | No `asyncpg`, DSN, key or password, billing/core/usage access or INSERT in the Neon gateway code; the only `Authorization` is the anonymous Neon Auth token; no `NEON_DATABASE_URL` in the app |
| `test_anonymous_grants_are_only_the_two_views` | The SQL grants `anonymous` exactly: USAGE on `public`, SELECT on the two views |
| `test_neon_models_match_plan_schema` | The 11 Pydantic models match the technical plan's columns exactly |

### `test_auth.py` — Clerk, login only (10)
| Test | Verifies |
| --- | --- |
| `test_pkce_pair` | S256 challenge matches the verifier |
| `test_access_token_is_verified_against_the_jwks` | RS256 JWT checked against the JWKS; wrong issuer, wrong client, wrong key and an expired token are refused |
| `test_identity_needs_an_organisation` | `sub`, `org_id` and the userinfo email; a token without `org_id` is refused |
| `test_protected_routes_require_login` | 401 on product routes; `/health` and system status stay public |
| `test_login_flow_hands_the_session_to_the_app_window` | `POST /auth/login` (S256, redirect `/callback`, scopes, no login hint) → `/callback` redirects to `/auth/done?status=ok` and sets **no cookie** → the poll sets `husk_session` once → state single-use → Clerk calls carry the User-Agent → logout revokes |
| `test_no_organisation_and_cancelled_are_reported_to_the_app` | The browser and the app window both learn why a login failed |
| `test_a_web_page_cannot_redeem_or_start_a_login` | A foreign Origin gets 403; an unknown poll id is "expired" |
| `test_tokens_are_never_persisted_or_logged` | Access token, refresh token, code and poll id are absent from logs and SQLite; the refresh token is in the keychain store |
| `test_no_guest_or_signup_routes` | The API has no guest, ospite, signup or register routes |
| `test_settings_roundtrip` | Theme, personality and `connectors_onboarded` saved |

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
| `test_logs_scrub_credentials_in_urls_and_headers` | Telegram bot tokens in URLs, OAuth `code`/`state`, bearer tokens and JWTs are scrubbed, including `%s` placeholders |

### `test_notifications.py` — the notification node (9)
| Test | Verifies |
| --- | --- |
| `test_a_notification_has_one_channel_…` · `test_sending_is_no_longer_an_action` | One channel per node, default desktop; the old `email`/`telegram`/`notify` action values no longer validate |
| `test_only_notifications_that_reach_others_count_as_sends` | `kinds_used` / `changes_sends`: Telegram and Gmail force a proposal, desktop does not |
| `test_desktop_notification_runs_without_leaking_the_message` | Prova shows nothing; a real run shows it; the log keeps `{channel, chars}` only |
| `test_the_report_made_by_an_earlier_step_can_be_attached` | The PDF path flows to a later notification; Telegram not set up → skipped |
| `test_the_user_sets_channel_and_recipients_by_hand` | `PUT …/nodes/{id}/notification`: normalised recipients, new version by the user, Undo, connector suggestion, clearing recipients |
| `test_manual_edits_are_validated_and_limited_to_notification_nodes` | Invalid or >10 addresses, unknown channel, other node kinds, missing node/automation, only the five fields change |
| `test_the_builder_is_told_about_notification_channels` | The channel block and the "never use an action to send" rule |
| `test_saved_automations_from_an_older_version_are_skipped_not_fatal` | Rows with the old action values are ignored by `list`/`all_active`/`get`: no crash at startup |

### `test_document_language.py` — summaries follow the document (13)
`detect_language` (Italian, English, short, numeric, mixed → English when unsure); ingest stores the language; old rows without one are detected when summarised; spreadsheet cards use the document language; `CARD_VERSION` 3 rebuilds old cards. (`test_i18n.py` checks the prompt: summary in the document's language, facts and tags in the user's.)

### Discovery counter (in `test_fast_discovery.py`, 3 new)
`done` and progress never decrease and event ids increase; the event hub replays only what a client missed and forgets an old attempt (no stale `failed` after a resume); a failed attempt cancels the documents still being read (no second counter).

### Frontend — `app/frontend/src/test/` (100)
- `discovery.test.tsx` (+2): `keepNewer` ignores a lower count of the same step; **one** `EventSource` per run however many events arrive, and a replayed lower count never shows.
- `markdown.test.tsx` (4): headings, numbered and bullet lists, quotes, code, bold, italic, https links; no HTML or `javascript:` links; the assistant's messages are formatted in the side chat while the user's own words and error texts stay as typed.
- `notification.test.tsx` (8): recipient parsing; the accent node with its chip; the chip opens the popup without selecting the node; the three channels with state; recipient validation, "add my address", save through the endpoint; a desktop notification sends no recipients.
- `ui.test.tsx` (3): login has no guest option · the Studio link appears only when a URL exists · agent markdown is rendered safely.
- `i18n.test.tsx` (29): catalogue parity · `translate()` interpolation, plurals and fallback · browser-language detection and saved choice · **no hard-coded UI text**: parses every `.tsx` (26 files, one test each) with the TypeScript compiler and fails on JSX text or `placeholder`/`title`/`aria-label`/`alt` literals with letters.
- `maintenance.test.tsx` (5): schedule helpers (past windows filtered, next blocking window) · acknowledgements remembered · the startup modal shows once and stays dismissed · windows more than 24 h away are ignored and the English message is used.
- `login.test.tsx` (7): the login starts, waits and polls; a missing organisation is explained (Italian); cancel; the "Authenticated, you can close this tab" page in both languages and its error variants; the "installation not active" screen.
- `connectors.test.tsx` (11): every guide step, link, tip and note exists in English and Italian · the guide popup with its links · the first-login popup (remembered once, hidden when seen or when all connectors work) · Telegram setup (detect chat → save → the token is emptied) and a refused token · Gmail validation, test and remove · the suggestion card sends the user to Settings ("Reconnect" when the connector stopped).

## Coverage: where it is thin

| Module | Cover | Why / what's missing |
| --- | --- | --- |
| `gateways/gateway/` | not measured | Transport, errors, rate gate, provider and decision adapter are tested against a mocked gateway; the real gateway is never called (the CTO runs the live calls). Needs a live smoke test |
| `core/automations/actions.py` | 51% | Desktop notifications, Excel output and writing to a custom folder are untested |
| `gateways/studio/client.py` | 53% | Real MCP sessions are mocked out |
| `core/events.py` | 54% | SSE back-pressure and the event replay buffer are untested |
| `endpoints/v1/system.py` | 54% | `/internal/studio-token` success path (needs a launch secret and a refresh token) |
| `gateways/clerk/tokens.py`, `gateways/connectors/secrets.py` | not measured | The OS keychain path is disabled in tests (in-memory store) |
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
| Code review (AI key, Oct 4) | Backup import read the user's key *after* the database was replaced | Obsolete: AI keys no longer exist on the machine |
| `test_telegram_errors_are_codes_and_never_carry_the_token` (Oct 6) | **httpx logs every request URL at INFO, and a Telegram Bot API URL contains the bot token**: it would have reached `husk.log` | `httpx`/`httpcore` loggers kept at WARNING; the log scrubber also redacts `bot<id>:<token>`, OAuth query parameters, bearer tokens and JWTs, including `%s` arguments; no uvicorn access log (the `/callback` request line carries the OAuth code) |
| Benchmarks (Oct 6) | The live benchmarks build services through the product wiring, which no longer has a direct provider | Direct Regolo/OpenRouter and Venice providers moved to `benchmarks/direct.py`; offline runs use the deterministic fake decision model |
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

- [ ] **Live gateway**: chat, `/v1/decide` on `jev-latest` (first live run, Oct 6: 502/429 because the gateway called Venice's chat endpoint instead of `/api/v1/decisions`), entitlement and usage with a real Clerk token; discovery quality and recall with the new decision model (the gates were tuned on Jev); how long a full discovery takes under the 60 requests/minute limit
- [ ] **Real Telegram and Gmail**: create a bot with @BotFather, detect the chat, send a test and a PDF; a Gmail App Password (and the failure when it is revoked)
- [ ] **Real model following the language instruction** (Italian vs English output from gpt-oss-120b on Regolo)
- [ ] **Desktop notification** on Windows (only macOS `osascript` and Linux `notify-send` are wired)
- [ ] **Real Clerk login**: the system browser flow end to end (webview and browser), the redirect `http://127.0.0.1:8765/callback`, an organisation in the token, refresh after 24 h, revoke on sign-out, and whether a second login reuses the browser's Clerk session
- [ ] **Real Neon Data API** (after enabling Neon Auth): the anonymous token is issued and accepted, PostgREST filters on the views, and that only the two views are readable
- [ ] **Running Husk Studio**: OTLP spans arriving, MCP tool names, the run deep-link route
- [ ] **Real scheduling**: a cron trigger firing and a watched folder reacting while Husk runs
- [ ] **UI end-to-end**: browser tests (e.g. Playwright) for upload → discovery → chat. Currently only a manual visual check
- [ ] **Windows and Linux** binaries and keychains; antivirus behaviour of the PyInstaller build
- [ ] **Load**: large document sets (thousands of files) and long chat histories
