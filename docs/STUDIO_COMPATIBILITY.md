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

## "Visualizza in Husk Studio"

Every discovery run, automation test run and builder chat turn runs inside an OpenTelemetry span; its trace id is stored. When something fails **and** Studio is alive, the UI shows **VISUALIZZA IN HUSK STUDIO**, linking to `HUSK_STUDIO_RUN_URL_TEMPLATE` (default `{base}/runs/{trace_id}`).

- [ ] Verify the Studio UI deep-link route and its run id ↔ trace id mapping, then adjust `HUSK_STUDIO_RUN_URL_TEMPLATE`.
- [ ] Freeze the MCP tool list (plan: `get_run`, `explain_failure`, `get_step_state`, `replay_from`). Studio's current docs list `list_runs`, `get_run`, `get_trace`, `get_span`, `list_errors`, `cost_breakdown`, `replay_run`. Husk handles both sets.
- [ ] Studio's MCP currently defaults to stdio. The plan requires Streamable HTTP at `/mcp`, so Studio must be started with `husk-ai mcp --transport http`.

Decoupling test: delete Studio entirely, and Husk still starts and runs discovery (`test_probe_when_studio_absent` plus the full e2e suite run without Studio).
