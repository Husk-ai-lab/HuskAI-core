"""Simulation — value of automations (roadmap step 6).

For every automation produced by discovery: run it locally (Prova) and model the saving:
  before = planted manual minutes × occurrences/week       (ground truth of the synthetic company)
  after  = measured automated runtime + 2 min human review per occurrence
Matched to planted tasks by keywords. Offline mode exercises execution with the scripted
model; the minutes saved come from the ground-truth model, so they are labelled "modelled".
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.agents.orchestrator import DiscoveryOrchestrator
from app.core.automations.service import run_automation
from app.data.models.automation import RunTrigger
from app.gateways.sqlite.repositories import AutomationRepo
from benchmarks.common import ORG, USER, isolated_services, load_dataset, md_table
from benchmarks.datasets.generate_synthetic import generate

REVIEW_MIN = 2.0
WEEKS_PER_YEAR = 46


def _match(title: str, tasks: list[dict]) -> dict | None:
    t = title.lower()
    best = max(tasks, key=lambda x: sum(k in t for k in x["keywords"]))
    return best if sum(k in t for k in best["keywords"]) >= 1 else None


async def run(live: bool) -> dict:
    folder = Path(__file__).parent / "datasets" / "generated" / "scale1"
    gt = json.loads(generate(folder, seed=7, scale=1).to_json())
    svc = isolated_services(live=live)
    if live:
        await svc.llm.resolve_model()  # type: ignore[attr-defined]
    load_dataset(svc, folder)
    orch = DiscoveryOrchestrator(svc.agent_context(ORG, USER), svc.events)
    r = orch.create_run(USER)
    await orch.execute(r.id)
    rows = []
    used: set[str] = set()
    for a in AutomationRepo(svc.db, ORG).list():
        run_ = await run_automation(svc, a, RunTrigger.prova, {}, USER)
        secs = ((run_.ended_at - run_.started_at).total_seconds()) if run_.ended_at else 0
        task = _match(a.title + " " + a.description, [t for t in gt["tasks"] if t["id"] not in used])
        if task:
            used.add(task["id"])
            before = task["occurrences_per_week"] * task["minutes_per_occurrence"] / 60
            after = task["occurrences_per_week"] * (secs / 60 + REVIEW_MIN) / 60
        else:
            before = after = 0.0
        rows.append({
            "automation": a.title, "run": run_.status.value, "runtime_s": round(secs, 1),
            "matched_task": task["title"] if task else "—",
            "before_h_week": round(before, 1), "after_h_week": round(after, 1),
            "saved_h_year": round((before - after) * WEEKS_PER_YEAR),
        })
    return {"mode": "live" if live else "offline (scripted model)", "rows": rows,
            "total_saved_h_year": sum(x["saved_h_year"] for x in rows),
            "success_rate": round(sum(x["run"] == "success" for x in rows) / max(len(rows), 1), 2)}


def report(r: dict) -> str:
    label = "" if r["mode"] == "live" else " (modelled from the synthetic company's ground truth; execution by scripted model)"
    return (f"## Simulation — value of automations\n\n**~{r['total_saved_h_year']} hours/year saved{label}.** "
            f"Local test-run success rate: {int(r['success_rate'] * 100)}%. Assumes {REVIEW_MIN:g} min human review per "
            f"occurrence and {WEEKS_PER_YEAR} working weeks.\n\n" + md_table(
                ["Automation", "Prova", "Runtime s", "Planted task", "Before h/week", "After h/week", "Saved h/year"],
                [[x["automation"], x["run"], x["runtime_s"], x["matched_task"], x["before_h_week"], x["after_h_week"],
                  x["saved_h_year"]] for x in r["rows"]]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    print(report(asyncio.run(run(a.live))))
