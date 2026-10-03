"""Run every benchmark and write benchmarks/results/REPORT.md (+ charts).

    uv run python -m benchmarks.report            # offline (deterministic, no API key needed)
    uv run python -m benchmarks.report --live     # include real-model discovery/automation simulations
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
from datetime import date

from benchmarks import bench_harness, bench_retrieval, simulate_automation_value, simulate_discovery_value
from benchmarks.common import RESULTS


def chart(retrieval: list[dict], discovery: dict) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    out = []
    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.bar([f"scale {r['scale']}\n{r['chunks']} chunks" for r in retrieval], [r["query_p50_ms"] for r in retrieval], color="#7456f6")
    ax.set_ylabel("query p50 (ms)")
    ax.set_title("Local BM25s search latency")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(RESULTS / "retrieval_latency.png", dpi=150)
    out.append("retrieval_latency.png")
    if discovery.get("hours_year_discovered") is not None:
        fig, ax = plt.subplots(figsize=(6, 3.2))
        ax.barh(["planted (truth)", "discovered"], [discovery["hours_year_planted"], discovery["hours_year_discovered"]],
                color=["#d9d5e6", "#7456f6"])
        ax.set_xlabel("hours / year")
        ax.set_title("Repetitive work discovered")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(RESULTS / "discovery_hours.png", dpi=150)
        out.append("discovery_hours.png")
    return out


def main(live: bool) -> None:
    RESULTS.mkdir(exist_ok=True)
    retrieval = [bench_retrieval.run(s) for s in (1, 3)]
    harness = bench_harness.run()
    discovery = asyncio.run(simulate_discovery_value.run(live))
    automations = asyncio.run(simulate_automation_value.run(live))
    charts = chart(retrieval, discovery)
    md = [
        "# Husk benchmarks",
        f"_{date.today().isoformat()} · {platform.system()} {platform.machine()} · Python {platform.python_version()} · "
        f"mode: {'live model' if live else 'offline'}_",
        "",
        "All data is a fictional, seeded company (`benchmarks/datasets/generate_synthetic.py`) with planted repetitive "
        "tasks. Offline numbers measure the deterministic machinery; AI-quality numbers come only from `--live` runs.",
        "",
        bench_retrieval.report(retrieval),
        "",
        bench_harness.report(harness),
        "",
        simulate_discovery_value.report(discovery),
        "",
        simulate_automation_value.report(automations),
        "",
    ] + [f"![{c}]({c})" for c in charts]
    (RESULTS / "REPORT.md").write_text("\n".join(md), "utf-8")
    (RESULTS / "results.json").write_text(json.dumps(
        {"retrieval": retrieval, "harness": harness, "discovery": discovery, "automations": automations},
        ensure_ascii=False, indent=2, default=str), "utf-8")
    print((RESULTS / "REPORT.md").read_text())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    main(ap.parse_args().live)
