# Benchmarks and value simulations

Oct 3, 2026 · @Marco

```bash
uv run python -m benchmarks.report          # offline, deterministic, no key needed (~10 s)
uv run python -m benchmarks.report --live   # adds real-model discovery + automation simulations (Regolo key in .env)
```

Output: `benchmarks/results/REPORT.md`, `results.json`, charts.

| File | Measures |
| --- | --- |
| `datasets/generate_synthetic.py` | Fictional "Officine Esempio S.r.l." with **planted** repetitive tasks and their true weekly cost, labelled retrieval queries, noise documents |
| `bench_retrieval.py` | BM25s ingest/index time, query p50/p95, recall@k, MRR at two corpus sizes |
| `bench_harness.py` | 30-fact cap and side-fact quality, note-validator precision/recall, heuristic signal ranking, data-tool exactness |
| `simulate_discovery_value.py` | Roadmap step 4: hours of repetitive work discovered vs planted truth, planted tasks found, tokens |
| `simulate_automation_value.py` | Roadmap step 6: before/after hours per automation (Prova executed locally) |

## Honesty rules (for investor material)

* **Offline mode** uses a scripted model (`test/fakes.py`). It proves the pipeline mechanics (7 steps, checkpoints, memory artefacts, local execution) and measures deterministic components. It **does not** produce discovery hours, and the report says so.
* Automation savings are **modelled** from the synthetic company's ground truth (minutes × occurrences) with an explicit 2-minute human review per occurrence and 46 working weeks.
* Only `--live` numbers speak about AI quality. Quote them with the model name, date and dataset seed.
* The dataset is synthetic. Results on real customer documents must be measured separately, with consent.

## Fictional company dataset (manual QA and end-to-end runs)

`uv run python -m benchmarks.datasets.generate_fictional_company` writes **Officine Valsorda S.r.l.** (invented brass-fittings manufacturer, 86 employees) to `benchmarks/fictional_company/` (git-ignored): 30 Italian documents in every ingest format, 9 planted repetitive tasks (~26 h/week), planted data defects and noise documents. Upload only `documents/`. `README.md` and `ground_truth.json` hold the expected Discovery findings and data-tool answers.
