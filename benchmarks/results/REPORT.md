# Husk benchmarks
_2026-10-03 · Darwin arm64 · Python 3.11.15 · mode: offline_

All data is a fictional, seeded company (`benchmarks/datasets/generate_synthetic.py`) with planted repetitive tasks. Offline numbers measure the deterministic machinery; AI-quality numbers come only from `--live` runs.

## Retrieval (BM25s, local)

| Scale | Docs | Chunks | Ingest s | Index ms | Query p50 ms | p95 ms | Recall@1 | Recall@3 | MRR |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 15 | 43 | 0.4 | 3.8 | 2.04 | 5.14 | 1.0 | 1.0 | 1.0 |
| 3 | 19 | 109 | 0.61 | 10.5 | 3.43 | 4.85 | 0.88 | 1.0 | 0.938 |

## Harness rules (deterministic, no LLM)

### Company profile (≤30 facts, side-fact replacement)

| Candidates | Kept | Cap respected | Mean importance kept | Random 30 | Ideal 30 | Replacements logged |
|---|---|---|---|---|---|---|
| 300 | 30 | True | 0.943 | 0.511 | 0.945 | 49 |

### Note integrity validator

| Bad notes | Rejected | Good notes | Wrongly rejected | Precision | Recall |
|---|---|---|---|---|---|
| 25 | 25 | 20 | 0 | 1.0 | 1.0 |

### Automation-signal ranking (heuristic layer only)

| Documents | k (true automatable) | Precision@k | Noise docs ranked last |
|---|---|---|---|
| 15 | 12 | 1.0 | True |

### Data-scientist tools (exact answers vs planted truth)

| Checks | Exact | Accuracy |
|---|---|---|
| 8 | 8 | 1.0 |

## Simulation — value of discovery

_Offline run: pipeline mechanics only (scripted model). Hours are not reported; run with `--live` for the real number. Planted ground truth: ~702 h/year._

| Mode | Model | Status | Docs | Time s | Opportunities | Activities | Facts | Tokens in/out |
|---|---|---|---|---|---|---|---|---|
| offline (scripted model) | scripted-test-model | completed | 15 | 0.4 | 2 | 8 | 1 | 0/0 |

| Opportunity | h/week | Priority |
|---|---|---|
| Confronto revisioni distinta base | 6.0 | alta |
| Report giornaliero di produzione | 4.0 | media |

## Simulation — value of automations

**~168 hours/year saved (modelled from the synthetic company's ground truth; execution by scripted model).** Local test-run success rate: 100%. Assumes 2 min human review per occurrence and 46 working weeks.

| Automation | Prova | Runtime s | Planted task | Before h/week | After h/week | Saved h/year |
|---|---|---|---|---|---|---|
| Confronto revisioni distinta base | success | 0.0 | Ricopiatura delle revisioni di distinta base nel gestionale | 3.8 | 0.1 | 168 |
| Confronto revisioni distinta base | success | 0.0 | — | 0.0 | 0.0 | 0 |

![retrieval_latency.png](retrieval_latency.png)