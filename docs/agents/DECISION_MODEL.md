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

## Jev on Venice (`app/gateways/decisions/jev.py`)

- **Call:** `POST https://api.venice.ai/api/v1/decisions` with `{model: "jev-latest", state, questions: {id: {type, instructions, criteria}}}`, `Authorization: Bearer <Venice key>`.
- **Batching:** batches of ≤ 64 questions, sent in parallel; 20 s timeout and one retry.
- **Errors:** 402 → `errors.decisions_quota`; 401/403 → `errors.ai_key_venice_rejected`.
- **Metering:** `usage.input_tokens` per step and model (`jev-latest`) and in `token_usage`.
- **Pricing and speed:** you pay for input tokens only (~$0.042 per million). Measured: **0.46 s** for 3 questions; **1.6 s** to link 30 documents (120 pairs); **0.6 s** to assess 9 networks.
- **Keys:** the Venice part of the user's combined key (see below), or `VENICE_API_KEY` in `.env` for development.
- **Beta:** schemas may change without notice. Keep the request building in this one file.

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

## Keys

- **One string after login:** `<Regolo key><VENICE_INFERENCE_KEY_…>`, split by regex in `key_store.split_keys`.
- **Verification:** Regolo `/models` and Venice `/models` (free); saved only if both pass.
- **Storage:** `user_ai_keys.decision_key` / `decision_hint` (migration 007). Never returned, never logged, stripped from backups, deleted on sign-out.

## Data protection

- The decision model receives **card text** (sentences, summaries, signals), never files.
- The upload authorisation and the key screen name both providers (GDPR guide G3).
- Venice AI states that it does not retain prompts.

## Moving to Serenity

1. Implement `DecisionModel` in `app/gateways/decisions/serenity.py`, using the prompt template from the Serenity spec: `<serenity> task=… lang=…`, STATE / QUESTION / lettered options, logits → calibrated softmax.
2. Map the five questions above to Serenity tasks. The wording already matches.
3. Re-run `benchmarks/bench_discovery_tokens.py --live` and retune the gates per decision for a target error rate (Serenity is calibrated per family).
