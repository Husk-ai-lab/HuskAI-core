"""Retrieval benchmark: BM25s index build time, query latency, recall@k and MRR."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from benchmarks.common import ORG, isolated_services, load_dataset, md_table
from benchmarks.datasets.generate_synthetic import generate


def run(scale: int = 1) -> dict:
    folder = Path(__file__).parent / "datasets" / "generated" / f"scale{scale}"
    gt = json.loads(generate(folder, seed=7, scale=scale).to_json())
    svc = isolated_services(live=False)
    t0 = time.perf_counter()
    docs = load_dataset(svc, folder)
    ingest_s = time.perf_counter() - t0
    ks = svc.knowledge(ORG)
    t0 = time.perf_counter()
    n_chunks = ks.reindex()
    index_s = time.perf_counter() - t0
    by_name = {d.id: d.filename for d in docs}
    lat, hits_at = [], {1: 0, 3: 0, 5: 0}
    rr = []
    for q in gt["queries"]:
        t0 = time.perf_counter()
        res = ks.search_chunks(q["query"], k=10)
        lat.append((time.perf_counter() - t0) * 1000)
        ranked_docs = list(dict.fromkeys(by_name[h.document_id] for h in res))
        first = next((i for i, f in enumerate(ranked_docs) if f in q["relevant"]), None)
        rr.append(1 / (first + 1) if first is not None else 0)
        for k in hits_at:
            hits_at[k] += int(first is not None and first < k)
    n = len(gt["queries"])
    return {
        "scale": scale, "documents": len(docs), "chunks": n_chunks,
        "ingest_s": round(ingest_s, 2), "index_ms": round(index_s * 1000, 1),
        "query_p50_ms": round(statistics.median(lat), 2), "query_p95_ms": round(sorted(lat)[int(0.95 * (n - 1))], 2),
        "recall@1": round(hits_at[1] / n, 2), "recall@3": round(hits_at[3] / n, 2), "recall@5": round(hits_at[5] / n, 2),
        "mrr": round(sum(rr) / n, 3),
    }


def report(results: list[dict]) -> str:
    return "## Retrieval (BM25s, local)\n\n" + md_table(
        ["Scale", "Docs", "Chunks", "Ingest s", "Index ms", "Query p50 ms", "p95 ms", "Recall@1", "Recall@3", "MRR"],
        [[r["scale"], r["documents"], r["chunks"], r["ingest_s"], r["index_ms"], r["query_p50_ms"], r["query_p95_ms"],
          r["recall@1"], r["recall@3"], r["mrr"]] for r in results],
    )


if __name__ == "__main__":
    rs = [run(s) for s in (1, 3)]
    print(report(rs))
