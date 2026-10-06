"""Discovery on the decision model (behind husk-gateway): linking and assessment."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.agents.explorer.agent import ExplorerAgent, _components
from app.core.agents.explorer.summarizer import Summarizer
from app.core.agents.planner.agent import PlannerAgent
from app.gateways.decisions import questions as q
from test.conftest import ORG, PROCEDURE_MD, USER, production_csv, upload_samples
from test.test_discovery_e2e import wait_run


def test_hours_and_score_conversion() -> None:
    hours, spread = q.expected_hours_per_week({"3": 1.0})  # 5–10 h/month → midpoint 7.5
    assert hours == round(7.5 / 4.33, 1) and spread == 0
    assert q.score_value({"8": 1.0}) == 0.85


# --- linking and assessment -------------------------------------------------------------------------
async def _cards(services):  # type: ignore[no-untyped-def]
    ks = services.knowledge(ORG)
    docs = [ks.add("Procedura_accettazione.md", PROCEDURE_MD.encode(), USER, True),
            ks.add("Accettazione_controlli.md", (PROCEDURE_MD + "\nControlli magazzino certificati.").encode(), USER, True),
            ks.add("Report.csv", production_csv(), USER, True),
            ks.add("Mensa.md", b"# Regolamento mensa\nLa mensa apre alle 12.", USER, True)]
    s = Summarizer(services.agent_context(ORG, USER))
    cards = [(await s.card_for(d))[0] for d in docs]
    mensa = cards[3]  # the scripted summarizer writes the same summary for every text: give this one its own
    mensa.sentence, mensa.summary, mensa.signal = "Regolamento della mensa aziendale.", "Orari della mensa.", 0.1
    mensa.signals = {"rep": 0.1, "manual": False, "dept": ["Servizi"], "sys": []}
    return cards


async def test_candidate_pairs_are_bounded(services) -> None:  # type: ignore[no-untyped-def]
    cards = await _cards(services)
    exp = ExplorerAgent(services.agent_context(ORG, USER))
    alias_of = {c.document_id: a for a, c in exp.aliases(cards).items()}
    pairs = exp.candidate_pairs(cards, alias_of)
    assert len(pairs) <= 4 * len(cards) and all(a < b for a, b in pairs)


async def test_linking_gates_edges_and_builds_networks(services) -> None:  # type: ignore[no-untyped-def]
    cards = await _cards(services)
    ctx = services.agent_context(ORG, USER)
    out = await ExplorerAgent(ctx).link_documents(cards, "run_t")
    assert out["edges"] and all(e["p"] >= ctx.settings.gate_related for e in out["edges"])
    assert all(e["type"] == "same_item" for e in out["edges"])
    docs_in_networks = {d for n in out["networks"].values() for d in n["docs"]}
    mensa = next(a for a, d in out["aliases"].items() if d == cards[3].document_id)
    assert mensa not in docs_in_networks  # unrelated, low-signal singleton left out
    calls = services.decisions.calls
    assert len(calls) == 2 and all(k.startswith("rel_") for k in calls[0]["questions"])  # links, then types
    net = ctx.memory.networks.get(next(iter(out["networks"].values()))["net_id"])
    notes = ctx.memory.networks.notes(net.id)
    assert notes and all(n.evidence for n in notes)  # card notes, no LLM


def test_oversized_components_are_split() -> None:
    nodes = [f"D{i:02d}" for i in range(1, 11)]
    edges = [{"a": nodes[i], "b": nodes[i + 1], "p": 0.9 if i != 4 else 0.61} for i in range(9)]
    groups = _components(edges, nodes, max_size=8)
    assert sorted(len(g) for g in groups) == [5, 5]  # cut at the weakest edge


async def test_assessment_and_flagged_documents_only(services) -> None:  # type: ignore[no-untyped-def]
    cards = await _cards(services)
    ctx = services.agent_context(ORG, USER)
    exp = ExplorerAgent(ctx)
    linked = await exp.link_documents(cards, "run_t")
    result = await exp.assess(linked["networks"], cards)
    nid, a = next(iter(result.items()))
    assert a["hours_per_week"] == round(7.5 / 4.33, 1) and a["repetitive"] >= ctx.settings.gate_repetitive
    assert len(a["deeper"]) == 1
    net = ctx.memory.networks.get(linked["networks"][nid]["net_id"])
    by_id = {c.document_id: c for c in cards}
    alias_of = {d: al for al, d in linked["aliases"].items()}
    flagged = linked["aliases"][a["deeper"][0]]
    context = PlannerAgent(ctx).context(net, [flagged], by_id, alias_of)
    passage_docs = {line.split("(")[1].split(")")[0] for line in context.split("\n") if line.startswith("[doc_")}
    assert passage_docs == {alias_of[flagged]}  # BM25 passages only from the flagged document


def test_discovery_on_the_decision_model(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    view = wait_run(client, run["id"])
    assert view["run"]["status"] == "completed", view
    steps = {s["step"]: s for s in view["steps"]}
    assert steps["repetitive"]["label"] == "Linking the documents"
    report = client.get(f"/api/v1/discovery/runs/{run['id']}/report").json()
    assert "jev-fake" in report["usage"]["by_model"]
    assert all(o["hours_per_week"] == round(7.5 / 4.33, 1) for o in report["items"])  # hours from the decision model


def test_discovery_needs_a_gateway_sign_in(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    """No usable Clerk token (e.g. the keychain was cleared): the local session ends and the UI shows the login."""
    from test.fakes import FakeDecisionModel

    services.decisions = FakeDecisionModel(configured=False)
    upload_samples(client)
    r = client.post("/api/v1/discovery/runs")
    assert r.status_code == 401 and r.json()["code"] == "errors.session_expired"
    assert client.get("/api/v1/user/me").status_code == 401


def test_model_constraint_violations_are_note_errors_not_crashes(services) -> None:  # type: ignore[no-untyped-def]
    from app.core.knowledge.memory.notes import NoteInvalid

    mem = services.memory(ORG)
    net = mem.networks.create("run_t", "Rete", [])
    with pytest.raises(NoteInvalid):
        mem.networks.write_note(net.id, title="x", claim="troppo corto", frequency="ogni giorno")
