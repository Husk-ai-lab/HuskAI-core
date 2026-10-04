"""Compare discovery benchmark runs and write benchmarks/results/DISCOVERY_BENCHMARK.md.

    uv run python -m benchmarks.compare_discovery \\
        benchmarks/results/discovery_baseline_agent_loop_live.json benchmarks/results/discovery_cards_live.json

Value signal assumptions (stated in the report): 46 working weeks per year (same as the Discovery
screen) and a fully loaded office cost of €30/hour for an Italian manufacturing SME clerk.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from benchmarks.bench_discovery_tokens import REGOLO_BASELINE, RESULTS, fmt_report

WEEKS_PER_YEAR = 46
EUR_PER_HOUR = 30.0


def _row(r: dict) -> dict:
    t = r.get("total", {})
    q = r.get("quality") or {}
    return {
        "label": r["label"], "completed": r.get("completed"), "seconds": r.get("seconds"),
        "calls": t.get("calls", 0), "tin": t.get("tokens_in", 0), "tout": t.get("tokens_out", 0),
        "cost": r.get("cost_usd", 0.0), "found": len(q.get("tasks_found", [])), "planted": q.get("planted_tasks"),
        "hours_est": q.get("hours_estimated_total"), "hours_truth": q.get("hours_truth_total"),
        "hours_matched_truth": q.get("hours_truth_matched"), "noise": len(q.get("noise_opportunities", [])),
        "notes": q.get("notes"), "notes_ev": q.get("notes_with_evidence"), "automations": q.get("automations"),
        "opps": q.get("opportunities", []),
    }


def _pct(a: float, b: float) -> str:
    return f"{(1 - b / a) * 100:.0f}%" if a else "–"


def report(runs: list[dict]) -> str:
    rows = [_row(r) for r in runs]
    base = rows[0]
    new = rows[-1]
    out = [
        "# Discovery benchmark — agent loop vs one-shot LLM vs decision model (Jev)",
        "",
        f"{date.today().isoformat()} · fictional company Officine Valsorda (30 documents, `benchmarks/fictional_company/`) · "
        "real orchestrator, live models: LLM via OpenRouter (`openai/gpt-oss-120b` main, `openai/gpt-oss-20b` summaries), "
        "decisions via Venice AI (`jev-latest`). OpenRouter is used for benchmarks only; the product calls Regolo.ai. "
        "Same documents and provider for every run. **Caveat:** only the decision-model run used OpenRouter's throughput "
        "routing (`provider.sort=throughput`), which removed most of the LLM latency; the two earlier runs did not.",
        "",
        "## Headline",
        "",
        "| | " + " | ".join(r["label"] for r in rows) + " |",
        "|---|" + "---|" * len(rows),
        "| Completed all 7 steps | " + " | ".join("yes" if r["completed"] else "**no**" for r in rows) + " |",
        "| Model calls | " + " | ".join(str(r["calls"]) for r in rows) + " |",
        "| Tokens in / out | " + " | ".join(f"{r['tin']:,} / {r['tout']:,}" for r in rows) + " |",
        "| Cost (USD, OpenRouter prices) | " + " | ".join(f"${r['cost']:.4f}" for r in rows) + " |",
        "| Wall time | " + " | ".join(f"{r['seconds']:.0f} s" for r in rows) + " |",
        "| Planted tasks found | " + " | ".join(f"{r['found']}/{r['planted']}" for r in rows) + " |",
        "| Hours/week proposed (planted: ~26) | " + " | ".join(str(r["hours_est"]) for r in rows) + " |",
        "| Noise opportunities | " + " | ".join(str(r["noise"]) for r in rows) + " |",
        "| Notes (with evidence) | " + " | ".join(f"{r['notes']} ({r['notes_ev']})" for r in rows) + " |",
        "| Automations drafted | " + " | ".join(str(r["automations"]) for r in rows) + " |",
        "",
        f"**Change from {base['label']} to {new['label']}:** calls −{_pct(base['calls'], new['calls'])}, "
        f"tokens −{_pct(base['tin'] + base['tout'], new['tin'] + new['tout'])}, "
        f"cost −{_pct(base['cost'], new['cost'])}, time −{_pct(base['seconds'], new['seconds'])}.",
        "",
        f"For reference, the first real run on Regolo (agent loop, Oct 3): {REGOLO_BASELINE['calls']} calls, "
        f"{REGOLO_BASELINE['tokens_in']:,} in / {REGOLO_BASELINE['tokens_out']:,} out, {REGOLO_BASELINE['note']}.",
        "",
        "## Signal of the value provided",
        "",
        f"Assumptions: {WEEKS_PER_YEAR} working weeks/year, €{EUR_PER_HOUR:.0f}/hour fully loaded office cost. Hours are the "
        "model's own estimates for its opportunities; \"matched\" counts only opportunities that correspond to a planted "
        "task (checked against the ground truth), so it is the conservative figure.",
        "",
        "| | " + " | ".join(r["label"] for r in rows) + " |",
        "|---|" + "---|" * len(rows),
    ]
    vals = []
    for r in rows:
        matched = r["hours_matched_truth"] or 0
        yearly_h = matched * WEEKS_PER_YEAR
        yearly_eur = yearly_h * EUR_PER_HOUR
        roi = yearly_eur / max(r["cost"] * 0.93, 1e-6)  # USD → EUR approx.
        vals.append((matched, yearly_h, yearly_eur, roi))
    out += [
        "| Planted hours/week covered by matched opportunities | " + " | ".join(f"{v[0]:.1f}" for v in vals) + " |",
        "| → hours/year | " + " | ".join(f"{v[1]:,.0f}" for v in vals) + " |",
        "| → €/year at €30/h | " + " | ".join(f"€{v[2]:,.0f}" for v in vals) + " |",
        "| AI cost of the discovery run | " + " | ".join(f"${r['cost']:.4f}" for r in rows) + " |",
        "| Value / AI cost | " + " | ".join(f"{v[3]:,.0f}×" for v in vals) + " |",
        "",
        "The AI cost of a discovery is negligible next to the work it surfaces in either design. What the new design "
        "changes is **whether the run finishes inside a provider's quota and how fast the user sees the result**: the "
        "agent loop exhausted Regolo's daily trial cap before drafting the automations; the summary-based run needs a "
        "fraction of the tokens.",
        "",
        "## Opportunities found",
        "",
    ]
    for r in rows:
        out.append(f"**{r['label']}**")
        out.append("")
        for o in r["opps"] or []:
            out.append(f"- {o['title']} — {o['hours']} h/week · sources: {', '.join(o['sources'][:3])}")
        if not r["opps"]:
            out.append("- (none)")
        out.append("")
    out += ["## Per step and per model", "", fmt_report(runs),
            "## Method and limits", "",
            "- One live run per design: model outputs vary between runs; treat differences of ±1 task as noise.",
            "- Quality matching: an opportunity counts for a planted task when it cites at least one of the task's documents "
            "and shares a keyword with the task title (or cites two of its documents).",
            "- Cost uses OpenRouter's list prices at run time; Regolo's pricing differs.",
            "- Reproduce: `uv run python -m benchmarks.bench_discovery_tokens --live --label <name>` then this script."]
    return "\n".join(out) + "\n"


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or [RESULTS / "discovery_baseline_agent_loop_live.json",
                                                 RESULTS / "discovery_fast_live.json",
                                                 RESULTS / "discovery_jev_live.json"]
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    md = report(runs)
    (RESULTS / "DISCOVERY_BENCHMARK.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
