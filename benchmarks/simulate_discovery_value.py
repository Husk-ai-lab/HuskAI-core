"""Simulation — value of discovery (roadmap step 4).

Runs the full discovery pipeline on the synthetic company and reports the hours of
repetitive work discovered, compared with the PLANTED ground truth.

    uv run python -m benchmarks.simulate_discovery_value          # offline: pipeline mechanics only
    uv run python -m benchmarks.simulate_discovery_value --live   # real model via Regolo.ai (.env key)

Offline mode uses a scripted model: it proves the pipeline runs end to end (7 steps,
checkpoints, memory artefacts) but its hours are NOT a discovery result and are not reported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.core.agents.orchestrator import DiscoveryOrchestrator
from benchmarks.common import ORG, USER, isolated_services, load_dataset, md_table
from benchmarks.datasets.generate_synthetic import generate

WEEKS_PER_YEAR = 46


def _covered(task: dict, texts: list[str]) -> bool:
    joined = " ".join(texts).lower()
    return sum(k in joined for k in task["keywords"]) >= 2


async def run(live: bool, scale: int = 1) -> dict:
    folder = Path(__file__).parent / "datasets" / "generated" / f"scale{scale}"
    gt = json.loads(generate(folder, seed=7, scale=scale).to_json())
    svc = isolated_services(live=live)
    if live:
        await svc.llm.resolve_model()  # type: ignore[attr-defined]
        if not svc.llm.configured:
            raise SystemExit("REGOLO_API_KEY mancante nel file .env")
    docs = load_dataset(svc, folder)
    orch = DiscoveryOrchestrator(svc.agent_context(ORG, USER), svc.events)
    run_ = orch.create_run(USER)
    t0 = time.perf_counter()
    final = await orch.execute(run_.id)
    elapsed = time.perf_counter() - t0
    rep = orch.report(run_.id)
    mem = svc.memory(ORG)
    texts = [o.title + " " + o.explanation for o in rep.items]
    for o in rep.items:
        texts += [n.title + " " + n.claim for n in mem.networks.notes(o.network_id)]
    covered = [t for t in gt["tasks"] if _covered(t, texts)]
    from app.gateways.sqlite.repositories import UsageRepo

    usage = UsageRepo(svc.db).summary(ORG)
    return {
        "mode": "live" if live else "offline (scripted model)",
        "model": svc.llm.model_name,
        "status": final.status.value,
        "documents": len(docs),
        "seconds": round(elapsed, 1),
        "opportunities": rep.opportunities,
        "repetitive_activities": rep.repetitive_activities,
        "facts": len(mem.repo.facts()),
        "hours_week_discovered": rep.total_hours_per_week if live else None,
        "hours_year_discovered": round(rep.total_hours_per_week * WEEKS_PER_YEAR) if live else None,
        "hours_week_planted": gt["total_hours_per_week"],
        "hours_year_planted": round(gt["total_hours_per_week"] * WEEKS_PER_YEAR),
        "planted_tasks": len(gt["tasks"]),
        "planted_tasks_found": len(covered) if live else None,
        "tokens_in": sum(u["tokens_in"] for u in usage),
        "tokens_out": sum(u["tokens_out"] for u in usage),
        "items": [{"title": o.title, "hours_week": o.hours_per_week, "priority": o.priority.value} for o in rep.items],
    }


def report(r: dict) -> str:
    head = "## Simulation — value of discovery\n\n"
    if r["hours_week_discovered"] is not None:
        head += (f"**Discovered repetitive work: ~{r['hours_year_discovered']} hours/year** "
                 f"(~{r['hours_week_discovered']} h/week, {WEEKS_PER_YEAR} working weeks) on a synthetic company whose planted "
                 f"repetitive work totals ~{r['hours_year_planted']} h/year. Planted tasks found: "
                 f"{r['planted_tasks_found']}/{r['planted_tasks']}.\n\n")
    else:
        head += ("_Offline run: pipeline mechanics only (scripted model). Hours are not reported; run with `--live` "
                 f"for the real number. Planted ground truth: ~{r['hours_year_planted']} h/year._\n\n")
    table = md_table(
        ["Mode", "Model", "Status", "Docs", "Time s", "Opportunities", "Activities", "Facts", "Tokens in/out"],
        [[r["mode"], r["model"], r["status"], r["documents"], r["seconds"], r["opportunities"], r["repetitive_activities"],
          r["facts"], f"{r['tokens_in']}/{r['tokens_out']}"]],
    )
    items = md_table(["Opportunity", "h/week", "Priority"], [[i["title"], i["hours_week"], i["priority"]] for i in r["items"]])
    return head + table + "\n\n" + items


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--scale", type=int, default=1)
    a = ap.parse_args()
    print(report(asyncio.run(run(a.live, a.scale))))
