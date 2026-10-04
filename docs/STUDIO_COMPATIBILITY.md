# Husk Studio compatibility

Oct 3, 2026 · @Marco

Husk and Husk Studio share **no code** (architecture rule 1, enforced by `test/test_studio.py::test_no_code_coupling_with_studio`). They meet only through public contracts:

| Contract | Husk side | Default |
| --- | --- | --- |
| Liveness probe | `GET {HUSK_STUDIO_UI_URL}/api/v1/runs` every 30 s | `http://127.0.0.1:7654` |
| OTLP/HTTP traces | Exporter enabled once Studio is detected; LangChain instrumented with OpenInference (same span format as Studio's `instrument_langgraph()`), `service.name=husk` | `{HUSK_STUDIO_OTLP_URL}/v1/traces` |
| MCP (Streamable HTTP) | `list_tools()` on every probe; only advertised tools are called | `http://127.0.0.1:7655/mcp` |
| Token endpoint for Studio | `GET /internal/studio-token` (launch secret) | — |

Loopback only: a non-loopback Studio URL is ignored (Studio is development-only and never sees production).

## Self-heal

When a discovery step fails and Studio's MCP is alive, Husk Op calls `explain_failure(run_id)` (or falls back to `get_trace` / `list_errors`, whichever exists) with the run's OpenTelemetry trace id, and retries the step once with the diagnosis injected (`harness/self_heal.py`). Without Studio the step simply fails and can be resumed.

Inside the Builder chat (see [HARNESS.md](HARNESS.md#self-heal-messages)):
- **On click:** a failed test run or chat error offers **Diagnose with Husk Studio** (only when the MCP is up). It sends a marked self-heal message; the Builder reads the trace with Studio's tools and always answers with a proposal.
- **Automatic, once:** a chat request that crashes is retried once with `explain_failure`'s diagnosis as a hint (`with_self_heal` in `agent/router.py`).

## Studio MCP tools in the Builder

`StudioClient.probe()` caches the advertised tools with their schemas. Only **read-only** names are kept (`get_*`, `list_*`, `explain_*`, `search_*`); anything else (e.g. `replay_run`, deletes) is never offered, and `call_tool` refuses it. `harness/studio_tools.py` turns them into `studio_<name>` LangChain tools. They are bound **only** for a self-heal message, when the automation's last run failed, or when the user asks about an error (keyword check, en/it), so normal turns don't pay for their schemas.

## Errors link to Studio, or to its install page

Every discovery run, automation test run and builder chat turn runs inside an OpenTelemetry span. Husk stores the **trace id and whether it was exported** (`traced`), never a precomputed link, so a link appears as soon as Studio is running. `StudioAction` (`lib/studio.ts` → `studioTarget`) shows on every blocking error (failed discovery, failed test, chat errors, automations in error, failed-action snackbars):

| Studio | Trace exported | Shown |
| --- | --- | --- |
| running | yes | **View in Husk Studio** → `HUSK_STUDIO_RUN_URL_TEMPLATE` (default `{base}/runs/{trace_id}`) |
| running | no | **View in Husk Studio** → Studio home (tooltip: run it again with Studio open) |
| not running | — | small **Install Husk Studio** → `HUSK_STUDIO_INSTALL_URL` (`app/config.py`, GitHub for now) |

Always visible besides errors: a collapsible **Husk Studio card** in the sidebar (Open / Install) and a pinned advice card on the Suggestions page (hidden for 30 days on request; not a DB suggestion, never counts toward the bulb's dot).

- [ ] Verify the Studio UI deep-link route and its run id ↔ trace id mapping, then adjust `HUSK_STUDIO_RUN_URL_TEMPLATE`.
- [ ] Freeze the MCP tool list (plan: `get_run`, `explain_failure`, `get_step_state`, `replay_from`). Studio's current docs list `list_runs`, `get_run`, `get_trace`, `get_span`, `list_errors`, `cost_breakdown`, `replay_run`. Husk handles both sets.
- [ ] Studio's MCP currently defaults to stdio. The plan requires Streamable HTTP at `/mcp`, so Studio must be started with `husk-ai mcp --transport http`.

Decoupling test: delete Studio entirely, and Husk still starts and runs discovery (`test_probe_when_studio_absent` plus the full e2e suite run without Studio).
