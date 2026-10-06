# Decision model — Jev now, Serenity later

Oct 4, 2026 · @Marco

Discovery's structural decisions (which documents are linked, how, whether a workflow is repetitive, how many hours it takes, which documents need re-reading) are **typed decisions with calibrated probabilities**, not generated text. They go to a System 1 decision model:

- **today:** Venice AI's **Jev** (`jev-latest`, beta);
- **tomorrow:** Husk AI's own **Serenity** (Qwen 2.5 0.5B + LoRA, nine decisions).

Discovery code only sees the interface in `app/gateways/decisions/base.py`, so the swap touches one gateway.

## Interface

```python
class DecisionModel(Protocol):
    model_name: str
    def configured_for(self, user_id: str) -> bool: ...
    async def ask(self, user_id, org_id, state: str, questions: dict[str, Question]) -> dict[str, Answer]: ...
```

- **Question:** `type` noul (yes/no) | choice (pick one) | score (ordered levels, lowest first), plus `instructions` and `criteria`.
- **Answer:** `p` (noul), `choice` + `probabilities` (choice), `score` = expected level position + `probabilities` (score), `confidence`.
- **One state, many questions:** all questions are answered against the same state in parallel, so a whole network costs one request.

## Through husk-gateway (`app/gateways/gateway/decisions.py`)

- **Call:** `POST {gateway}/v1/decide` with `{state, lang, model, questions: [{id, type: choice|score, options, text}]}`, `Authorization: Bearer <Clerk access token>`. The answer is `{id: {answer, p, probabilities}}`. The gateway calls Venice with one forward pass per question.
- **Adapter** (pure functions, unit-tested): `noul` → a `choice` over its two criteria texts (`p` = probability of the "true" one); `choice` → options are the criteria descriptions, mapped back to the keys; `score` → the gateway's `score` type when it has the 10 bands it calibrates for (repetitiveness), otherwise a `choice` over the level labels (the 8 hour buckets). In every case `Answer.probabilities` is keyed by level index, so the Explorer's expected-value maths is unchanged.
- **Limits handled:** ≤ 16 questions per request (batches run in parallel), `state` ≤ 12 000 characters (long summaries are shortened evenly, aliases and titles kept), 60 requests/minute/user (client-side rate gate shared with chat).
- **Model:** `jev-latest` (`config.DECISION_MODEL`). Jev is served by Venice's own **`POST /api/v1/decisions`** endpoint (typed `noul`/`choice`/`score` questions answered together over one state), **not** by chat completions. The gateway must call that endpoint for Jev: if it calls `/chat/completions` with logprobs, Venice refuses (the gateway answers 502 "decision provider rejected the request") and per-question fan-out then trips Venice's rate limit (429). Oct 6: this is exactly what the first live run showed. **The gates (`gate_related`, `gate_deeper`, `gate_repetitive`) were tuned on Jev.**
- **Errors:** 401 → sign in again; 403 → installation not active; 429/5xx → `errors.gateway_busy` / `errors.gateway_unavailable`.
- **Metering:** the gateway meters decisions itself and returns no token counts; the local per-run report estimates them (≈ 4 characters per token in, one token out per question).
- **Benchmarks** can still call Venice directly (`benchmarks/direct.py`, key from `.env`): the product never does.

## The decisions discovery asks (`app/gateways/decisions/questions.py`)

| Serenity decision | Type | Used in | Gate |
|---|---|---|---|
| `doc_related` | noul | step 4: one per candidate pair | `GATE_RELATED` 0.75 |
| `relation_type` | choice: handoff · revision · same job/order/item · rule and record · duplicate | step 4: one per kept edge | — |
| `repetitiveness_signal` | score, 10 bands 0–1 | step 5: one per network | `GATE_REPETITIVE` 0.5 |
| `hours_wasted` | score, 8 buckets per month (under 1 h … over 100 h) | step 5: one per network | → hours/week = expected bucket midpoint ÷ 4.33 |
| `needs_deeper_research` | noul | step 5: one per document of a network | `GATE_DEEPER` 0.6 |

- **Wording** follows the Serenity definitions and the user's language (it/en). For example, "same company, department or vocabulary is not enough" for `doc_related`.
- **Below a gate an answer counts as no** (CTO decision): uncertain edges are left out and uncertain documents are not re-read.
- **Gates** live in `app/config.py` and can be overridden by environment variable.
- Not used yet: `explore_next`, `navigation`, `enough_evidence`, `document_type`.

## State

The state is always **document cards** (see [EXPLORER_AGENT.md](EXPLORER_AGENT.md)):
- one line per document, `D07 | title | sentence | rep=… manual=yes freq=… dept=… sys=…`;
- then its summary;
- for a network, also its links (`D03 ↔ D07: Data handoff between departments`).

It is plain text, not JSON, and about 7–10k tokens for 30 documents.

## Credentials

None on this computer: the request carries the user's Clerk access token; the gateway holds the Venice key.

## Data protection

- The decision model receives **card text** (sentences, summaries, signals), never files.
- The upload authorisation names both providers and the gateway (GDPR guide G3).
- Venice AI states that it does not retain prompts.

## Moving to Serenity

1. Implement `DecisionModel` in `app/gateways/decisions/serenity.py`, using the prompt template from the Serenity spec: `<serenity> task=… lang=…`, STATE / QUESTION / lettered options, logits → calibrated softmax.
2. Map the five questions above to Serenity tasks. The wording already matches.
3. Re-run `benchmarks/bench_discovery_tokens.py --live` and retune the gates per decision for a target error rate (Serenity is calibrated per family).
