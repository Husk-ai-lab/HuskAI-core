"""Discovery benchmark — tokens per step and per model, time, cost and quality.

Runs the real discovery orchestrator on the fictional company (Officine Valsorda, 30 documents,
`benchmarks/fictional_company/`) in an isolated data dir, meters every model call per step and per
model, and scores the result against `ground_truth.json`.

    uv run python -m benchmarks.bench_discovery_tokens                    # offline: scripted model, estimated tokens
    uv run python -m benchmarks.bench_discovery_tokens --live --label cards --budget 400000
    uv run python -m benchmarks.bench_discovery_tokens --live --provider regolo

Live providers (keys from the git-ignored `.env`, never committed):
  * openrouter (default for benchmarks) — OPENROUTER_API_KEY, models openai/gpt-oss-120b + openai/gpt-oss-20b.
    Benchmark only: the product calls Regolo.ai (Italy, zero retention). The corpus is fictional.
  * regolo — REGOLO_API_KEY, gpt-oss-120b + gpt-oss-20b.

Offline numbers measure the pipeline's mechanics (calls, prompt sizes ≈ characters / 4), not AI quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.agents.orchestrator import DiscoveryOrchestrator  # noqa: E402
from app.core.metering import BudgetExceeded, Meter, metered  # noqa: E402
from app.data.models.discovery import DiscoveryStepId, RunStatus  # noqa: E402
from app.data.models.knowledge import NetworkStatus  # noqa: E402
from app.data.models.user import User  # noqa: E402
from app.gateways.sqlite.repositories import AutomationRepo, DiscoveryRepo, UserRepo  # noqa: E402
from app.helpers.settings import Settings  # noqa: E402
from app.main import build_services  # noqa: E402

DATASET = ROOT / "benchmarks" / "fictional_company"
RESULTS = ROOT / "benchmarks" / "results"
ORG, USER = "org_bench", "user_bench"

PROVIDERS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY",
                   "model": "openai/gpt-oss-120b", "summary": "openai/gpt-oss-20b"},
    "regolo": {"base_url": "https://api.regolo.ai/v1", "key": "REGOLO_API_KEY",
               "model": "gpt-oss-120b", "summary": "gpt-oss-20b"},
}
# OpenRouter list prices (USD per token), used for cost estimates when the live price list is unavailable.
FALLBACK_PRICES = {"gpt-oss-120b": (0.037e-6, 0.17e-6), "gpt-oss-20b": (0.018e-6, 0.09e-6),
                   "jev": (0.042e-6, 0.0)}  # Venice Jev: input tokens only
# Measured on Oct 3, 2026 with the previous pipeline (Regolo, same 30 documents): see docs/DISCOVERY_EFFICIENCY.md
REGOLO_BASELINE = {"label": "agent loop (Regolo, measured)", "calls": 127, "tokens_in": 957_716, "tokens_out": 93_335,
                   "seconds": 424, "completed": False, "note": "stopped at step 7 by Regolo's daily cap (402)"}

STOP = {"della", "delle", "degli", "dalla", "nelle", "sulle", "with", "from", "that", "this", "ogni", "come",
        "sono", "viene", "vengono", "anche", "the", "and", "per", "dei", "con", "una", "uno"}


def env_value(name: str) -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def build(live: bool, provider: str) -> Any:
    data = Path(tempfile.mkdtemp(prefix="husk-bench-disc-"))
    overrides: dict[str, Any] = {"HUSK_ENV": "test", "HUSK_DATA_DIR": data, "NEON_DATA_API_URL": None,
                                 "ZITADEL_ISSUER": None, "ZITADEL_CLIENT_ID": None,
                                 "HUSK_STUDIO_UI_URL": "http://127.0.0.1:9", "HUSK_STUDIO_MCP_URL": "http://127.0.0.1:9/mcp"}
    if live:
        p = PROVIDERS[provider]
        key = env_value(p["key"])
        if not key:
            raise SystemExit(f"{p['key']} is missing from .env")
        overrides |= {"REGOLO_API_KEY": key, "REGOLO_BASE_URL": p["base_url"], "REGOLO_MODEL": p["model"],
                      "HUSK_SUMMARY_MODEL": p["summary"], "VENICE_API_KEY": env_value("VENICE_API_KEY")}
        if not overrides["VENICE_API_KEY"]:
            raise SystemExit("VENICE_API_KEY is missing from .env (decision model)")
    else:
        overrides["REGOLO_API_KEY"] = None
    svc = build_services(Settings(**overrides))
    if not live:
        from test.fakes import FakeDecisionModel, FakeProvider

        svc.llm = FakeProvider(estimate=True, summary_model="gpt-oss-20b (simulated)")  # type: ignore[assignment]
        svc.decisions = FakeDecisionModel()  # type: ignore[assignment]
        svc.llm.model_name = "gpt-oss-120b (simulated)"
    UserRepo(svc.db).upsert(User(id=USER, org_id=ORG, org_name="Officine Valsorda (fictional)"))
    return svc


def ensure_dataset() -> Path:
    docs = DATASET / "documents"
    if not docs.exists():
        from benchmarks.datasets.generate_fictional_company import generate

        generate(DATASET)
    return docs


def upload(svc: Any, folder: Path, subset: int | None) -> list:
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    if subset:
        files = files[:subset]
    ks = svc.knowledge(ORG)
    return [ks.add(f.name, f.read_bytes(), USER, True, "it", str(f.relative_to(folder))) for f in files]


def prices(live: bool) -> dict[str, tuple[float, float]]:
    if live:
        try:
            import httpx

            data = httpx.get("https://openrouter.ai/api/v1/models", timeout=15).json()["data"]
            live = {m["id"].split("/")[-1]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"]))
                    for m in data if "gpt-oss" in m["id"] and ":" not in m["id"]}
            return live | {"jev": FALLBACK_PRICES["jev"]}
        except Exception:
            pass
    return dict(FALLBACK_PRICES)


def cost(by_model: dict[str, dict[str, int]], table: dict[str, tuple[float, float]]) -> float:
    total = 0.0
    for model, m in by_model.items():
        key = next((k for k in table if k in model), None)
        if key:
            pin, pout = table[key]
            total += m["tokens_in"] * pin + m["tokens_out"] * pout
    return round(total, 5)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ù]{4,}", text.lower()) if w not in STOP}


def score(svc: Any, run_id: str, truth: dict) -> dict[str, Any]:
    """Planted tasks found, hours, noise and evidence, from the run's opportunities and notes."""
    runs = DiscoveryRepo(svc.db, ORG)
    opps = runs.opportunities(run_id)
    names = {d.id: d.filename for d in svc.knowledge(ORG).docs.list()}
    noise = {Path(n).name for n in truth.get("noise_documents", [])}
    tasks = [t for t in truth["tasks"] if t.get("automatable", True)]
    found, matched_hours_truth, matched_hours_est = [], 0.0, 0.0
    for t in tasks:
        tdocs = {Path(d).name for d in t["documents"]}
        tw = _words(t["title"])
        for o in opps:
            odocs = {names.get(d, d) for d in o.source_document_ids}
            if (odocs & tdocs) and (len(tw & _words(o.title + " " + o.explanation)) >= 1 or len(odocs & tdocs) >= 2):
                found.append(t["id"])
                matched_hours_truth += t["hours_per_week"]
                matched_hours_est += o.hours_per_week
                break
    noisy = [o.title for o in opps if o.source_document_ids and {names.get(d, d) for d in o.source_document_ids} <= noise]
    mem = svc.memory(ORG)
    notes = [n for net in mem.repo.networks() if net.status != NetworkStatus.discarded for n in mem.networks.notes(net.id)]
    with_evidence = sum(1 for n in notes if n.evidence)
    return {
        "planted_tasks": len(tasks), "tasks_found": sorted(set(found)),
        "recall": round(len(set(found)) / max(len(tasks), 1), 2),
        "hours_truth_total": truth.get("total_hours_per_week"),
        "hours_estimated_total": round(sum(o.hours_per_week for o in opps), 1),
        "hours_truth_matched": round(matched_hours_truth, 1), "hours_estimated_matched": round(matched_hours_est, 1),
        "opportunities": [{"title": o.title, "hours": o.hours_per_week, "sources": [names.get(d, d) for d in o.source_document_ids]}
                          for o in opps],
        "noise_opportunities": noisy,
        "notes": len(notes), "notes_with_evidence": with_evidence,
        "facts": len(mem.repo.facts()),
        "automations": len(AutomationRepo(svc.db, ORG).list()),
    }


async def run(live: bool, provider: str, label: str, budget: int | None, subset: int | None,
              stage: str | None = None) -> dict[str, Any]:
    folder = ensure_dataset()
    truth = json.loads((DATASET / "ground_truth.json").read_text(encoding="utf-8"))
    svc = build(live, provider)
    if live:
        await svc.llm.resolve_model()
    docs = upload(svc, folder, subset)
    ctx = svc.agent_context(ORG, USER, language="it")
    orch = DiscoveryOrchestrator(ctx, svc.events)
    meter = Meter(budget=budget)
    started = time.perf_counter()
    run = orch.create_run(USER)
    error = None
    with metered(meter):
        try:
            run = await orch.execute(run.id, until=DiscoveryStepId(stage) if stage else None)
        except BudgetExceeded as exc:
            error = str(exc)
    seconds = round(time.perf_counter() - started, 1)
    run = DiscoveryRepo(svc.db, ORG).get(run.id) or run
    steps = []
    for s in DiscoveryRepo(svc.db, ORG).steps(run.id):
        u = (s.output or {}).get("usage") or {"by_model": {}, "total": {"calls": 0, "tokens_in": 0, "tokens_out": 0}}
        dur = (s.finished_at - s.started_at).total_seconds() if s.started_at and s.finished_at else None
        steps.append({"step": s.step.value, "status": s.status.value, "seconds": round(dur, 1) if dur else None, **u,
                      "timings": (s.output or {}).get("timings")})
    usage = meter.as_dict()
    table = prices(live)
    return {
        "label": label, "mode": "live" if live else "offline (estimated)", "provider": provider if live else "scripted",
        "models": {"main": svc.llm.model_name, "summary": getattr(svc.llm, "summary_model_name", None)},
        "documents": len(docs), "status": run.status.value, "completed": run.status == RunStatus.completed,
        "stage": stage, "error": error or run.error, "seconds": seconds, "steps": steps, **usage,
        "cost_usd": cost(usage["by_model"], table),
        "quality": score(svc, run.id, truth) if live and not stage else None,
    }


def fmt_report(results: list[dict[str, Any]]) -> str:
    lines = ["## Discovery benchmark — tokens, time, cost, quality", ""]
    for r in results:
        t = r.get("total", {})
        lines += [f"### {r['label']} — {r['mode']}, {r['provider']}", "",
                  f"Models: main `{r['models']['main']}`, summary `{r['models']['summary']}` · documents: {r['documents']} · "
                  f"status: **{r['status']}** · time: **{r['seconds']} s** · calls: **{t.get('calls')}** · "
                  f"tokens in/out: **{t.get('tokens_in'):,} / {t.get('tokens_out'):,}** · cost: **${r['cost_usd']}**", "",
                  "| Step | Status | Time (s) | Model | Calls | Tokens in | Tokens out |", "|---|---|---|---|---|---|---|"]
        for s in r["steps"]:
            models = s.get("by_model") or {"—": {"calls": 0, "tokens_in": 0, "tokens_out": 0}}
            for i, (m, u) in enumerate(models.items()):
                lines.append(f"| {s['step'] if i == 0 else ''} | {s['status'] if i == 0 else ''} | "
                             f"{(s['seconds'] if s['seconds'] is not None else '–') if i == 0 else ''} | {m} | "
                             f"{u['calls']} | {u['tokens_in']:,} | {u['tokens_out']:,} |")
        ext = next((s for s in r["steps"] if s["step"] == "extract"), None)
        tim = (ext or {}).get("timings") or {}
        if tim:
            slow = sorted(tim.values(), key=lambda x: -x["seconds"])[:5]
            lines += ["", "Slowest documents in step 3: " + ", ".join(
                f"{x['file']} {x['seconds']} s ({x['model'] or 'computed'})" for x in slow)]
        q = r.get("quality")
        if q:
            lines += ["", f"Quality vs ground truth: **{len(q['tasks_found'])}/{q['planted_tasks']} planted tasks found** "
                          f"(recall {q['recall']}) · hours estimated {q['hours_estimated_total']} vs planted {q['hours_truth_total']} "
                          f"(matched: {q['hours_estimated_matched']} vs {q['hours_truth_matched']}) · noise opportunities: "
                          f"{len(q['noise_opportunities'])} · notes with evidence: {q['notes_with_evidence']}/{q['notes']} · "
                          f"automations drafted: {q['automations']}"]
        if r.get("error"):
            lines += ["", f"Stopped: {r['error']}"]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="openrouter")
    ap.add_argument("--label", default="current")
    ap.add_argument("--budget", type=int, default=400_000, help="stop after this many tokens (live)")
    ap.add_argument("--subset", type=int, default=None, help="only the first N documents")
    ap.add_argument("--yes", action="store_true", help="don't ask before a live run")
    ap.add_argument("--stage", choices=[s.value for s in DiscoveryStepId], default=None,
                    help="stop after this step (measurement gates)")
    a = ap.parse_args()
    if a.live and not a.yes:
        if input(f"Live run on {a.provider} (budget {a.budget:,} tokens). Continue? [y/N] ").strip().lower() != "y":
            raise SystemExit(0)
    result = asyncio.run(run(a.live, a.provider, a.label, a.budget if a.live else None, a.subset, a.stage))
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"discovery_{a.label}{'_' + a.stage if a.stage else ''}_{'live' if a.live else 'offline'}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(fmt_report([result]))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
