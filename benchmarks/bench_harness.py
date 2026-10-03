"""Harness benchmark: the rules that keep agent memory trustworthy, measured.

* Company profile: never above 30 facts; side facts replace weaker facts only.
* Note integrity: incomplete / unsupported notes are rejected before storage.
* Signal ranking (heuristic layer only, no LLM): automatable docs ranked above noise.
* Data-scientist tools: exact answers on planted spreadsheet facts.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from app.core.agents.explorer.Rag.signal import heuristic_signal
from app.core.knowledge.memory.notes import NoteInvalid
from app.data.models.knowledge import EvidenceRef, FactCandidate
from benchmarks.common import ORG, isolated_services, load_dataset, md_table
from benchmarks.datasets.generate_synthetic import generate


async def facts_bench(svc) -> dict:  # type: ignore[no-untyped-def]
    mem = svc.memory(ORG)
    rng = random.Random(3)
    imps = []
    for i in range(300):
        imp = round(rng.random(), 3)
        imps.append(imp)
        await mem.profile.add(FactCandidate(text=f"Fatto {i}: macchina{i} reparto{i % 17} codice{i * 7}", importance=imp))
    kept = mem.repo.facts()
    top30 = sorted(imps, reverse=True)[:30]
    return {
        "candidates": 300, "kept": len(kept), "cap_respected": len(kept) <= 30,
        "mean_importance_kept": round(sum(f.importance for f in kept) / len(kept), 3),
        "mean_importance_random_30": round(sum(imps) / len(imps), 3),
        "mean_importance_ideal_30": round(sum(top30) / 30, 3),
        "replacements_logged": len(mem.repo.fact_history()),
    }


def notes_bench(svc, doc_id: str) -> dict:  # type: ignore[no-untyped-def]
    nets = svc.memory(ORG).networks
    net = nets.create("bench", "bench", [doc_id])
    ev = [EvidenceRef(document_id=doc_id)]
    good = [dict(title=f"Attività {i}", claim=f"Ogni sera il reparto {i} ricopia i dati di turno nel foglio condiviso.",
                 frequency="ogni sera", evidence=ev) for i in range(20)]
    bad = (
        [dict(title="Troncata", claim="Ogni sera il reparto ricopia i dati del", frequency="ogni sera", evidence=ev)] * 5
        + [dict(title="Senza evidenza", claim="Ogni sera il reparto ricopia i dati di turno.", frequency="ogni sera")] * 5
        + [dict(title="Doc inventato", claim="Ogni sera il reparto ricopia i dati di turno.", frequency="ogni sera",
                evidence=[EvidenceRef(document_id="doc_inesistente")])] * 5
        + [dict(title="Segnaposto", claim="Il reparto TODO completa il registro settimanale.", frequency="settimanale", evidence=ev)] * 5
        + [dict(title="Senza frequenza", claim="Il reparto ricopia i dati di turno nel foglio.", evidence=ev)] * 5
    )
    tp = fp = 0
    for n in bad:
        try:
            nets.write_note(net.id, **n)
        except NoteInvalid:
            tp += 1
    for n in good:
        try:
            nets.write_note(net.id, **n)
        except NoteInvalid:
            fp += 1
    return {"bad_notes": len(bad), "rejected_bad": tp, "good_notes": len(good), "rejected_good": fp,
            "precision": round(tp / max(tp + fp, 1), 3), "recall": round(tp / len(bad), 3)}


def signal_bench(svc, gt: dict) -> dict:  # type: ignore[no-untyped-def]
    ks = svc.knowledge(ORG)
    scored = []
    tools = ks.tabular()
    for d in ks.docs.list():
        prof = ks.docs.profiles(d.id)
        per = [tools.detect_periodicity(d.id, sheet=p.sheet) for p in prof[:2]] if d.kind.is_tabular else []
        s, _ = heuristic_signal(d, ks.docs.markdown(d.id), prof, per)
        scored.append((s, d.filename))
    scored.sort(reverse=True)
    positives = set(gt["automatable_documents"])
    k = len(positives)
    top = [f for _, f in scored[:k]]
    noise_rank = [i for i, (_, f) in enumerate(scored) if f in gt["noise_documents"]]
    return {"documents": len(scored), "precision@k": round(sum(f in positives for f in top) / k, 3), "k": k,
            "noise_ranked_last": min(noise_rank) >= len(scored) - len(noise_rank)}


def tabular_bench(svc, docs: list) -> dict:  # type: ignore[no-untyped-def]
    import pandas as pd

    ks = svc.knowledge(ORG)
    t = ks.tabular()
    checks = ok = 0
    for d in docs:
        if not d.filename.startswith("Distinta") or "RevC" not in d.filename:
            continue
        path = ks.file_path(d)
        truth = pd.read_excel(path)["Fornitore preferenziale"].isna().sum()
        got = t.missing_values(d.id)["missing_by_column"].get("Fornitore preferenziale", 0)
        checks += 1
        ok += int(got == truth)
        b = next(x for x in docs if x.filename == d.filename.replace("RevC", "RevB"))
        diff = t.compare_versions(b.id, d.id, "Codice")
        checks += 1
        ok += int(diff["changed_count"] > 0)
    rep = next(d for d in docs if d.filename.startswith("Report_Produzione"))
    checks += 1
    ok += int(t.detect_periodicity(rep.id)["cadence"] == "daily")
    truth_scarti = pd.read_csv(ks.file_path(rep))["Scarti"].sum()
    checks += 1
    ok += int(t.aggregate(rep.id, "Scarti", "sum")["sum"] == truth_scarti)
    return {"checks": checks, "exact": ok, "accuracy": round(ok / checks, 3)}


def run() -> dict:
    folder = Path(__file__).parent / "datasets" / "generated" / "scale1"
    gt = json.loads(generate(folder, seed=7, scale=1).to_json())
    svc = isolated_services(live=False)
    docs = load_dataset(svc, folder)
    return {
        "facts": asyncio.run(facts_bench(svc)),
        "notes": notes_bench(svc, docs[0].id),
        "signal": signal_bench(svc, gt),
        "tabular": tabular_bench(svc, docs),
    }


def report(r: dict) -> str:
    f, n, s, t = r["facts"], r["notes"], r["signal"], r["tabular"]
    return "\n\n".join([
        "## Harness rules (deterministic, no LLM)",
        "### Company profile (≤30 facts, side-fact replacement)\n\n" + md_table(
            ["Candidates", "Kept", "Cap respected", "Mean importance kept", "Random 30", "Ideal 30", "Replacements logged"],
            [[f["candidates"], f["kept"], f["cap_respected"], f["mean_importance_kept"], f["mean_importance_random_30"],
              f["mean_importance_ideal_30"], f["replacements_logged"]]]),
        "### Note integrity validator\n\n" + md_table(
            ["Bad notes", "Rejected", "Good notes", "Wrongly rejected", "Precision", "Recall"],
            [[n["bad_notes"], n["rejected_bad"], n["good_notes"], n["rejected_good"], n["precision"], n["recall"]]]),
        "### Automation-signal ranking (heuristic layer only)\n\n" + md_table(
            ["Documents", "k (true automatable)", "Precision@k", "Noise docs ranked last"],
            [[s["documents"], s["k"], s["precision@k"], s["noise_ranked_last"]]]),
        "### Data-scientist tools (exact answers vs planted truth)\n\n" + md_table(
            ["Checks", "Exact", "Accuracy"], [[t["checks"], t["exact"], t["accuracy"]]]),
    ])


if __name__ == "__main__":
    print(report(run()))
