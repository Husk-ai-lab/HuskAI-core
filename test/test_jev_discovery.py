"""Discovery on the decision model (Venice Jev now, Serenity later): client, keys, linking, assessment."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.agents.explorer.agent import ExplorerAgent, _components
from app.core.agents.explorer.summarizer import Summarizer
from app.core.agents.planner.agent import PlannerAgent
from app.core.metering import Meter, metered
from app.gateways.decisions import questions as q
from app.gateways.decisions.base import DecisionUnavailable, Question
from app.gateways.decisions.jev import BATCH, JevClient
from app.gateways.regolo.key_store import UserAiKeyStore, mask, split_keys
from app.gateways.sqlite.repositories import UsageRepo
from app.helpers.settings import Settings
from test.conftest import ORG, PROCEDURE_MD, USER, production_csv, upload_samples
from test.test_discovery_e2e import wait_run

DECISIONS = "https://api.venice.ai/api/v1/decisions"
VENICE = "VENICE_INFERENCE_KEY_abc123_XYZ-9"


# --- client ---------------------------------------------------------------------------------------
@respx.mock
async def test_jev_request_shape_batching_and_metering(services) -> None:  # type: ignore[no-untyped-def]
    sent: list[dict] = []

    def reply(request):  # type: ignore[no-untyped-def]
        body = json.loads(request.content)
        sent.append(body)
        answers = {k: {"type": "noul", "noul": 0.9} for k in body["questions"]}
        return httpx.Response(200, json={"model": "jev-latest", "answers": answers,
                                         "usage": {"input_tokens": 100, "output_tokens": 5}})

    respx.post(DECISIONS).mock(side_effect=reply)
    s = Settings(HUSK_ENV="test", VENICE_API_KEY=VENICE)
    client = JevClient(s, UserAiKeyStore(services.db), UsageRepo(services.db))
    qs = {f"rel_{i}": q.doc_related("D01", "D02", "en") for i in range(BATCH + 6)}
    meter = Meter()
    with metered(meter):
        ans = await client.ask(USER, ORG, "STATE", qs)
    assert len(ans) == BATCH + 6 and ans["rel_0"].p == 0.9
    assert len(sent) == 2 and sent[0]["model"] == "jev-latest" and sent[0]["state"] == "STATE"
    assert sent[0]["questions"]["rel_0"]["type"] == "noul" and "criteria" in sent[0]["questions"]["rel_0"]
    assert respx.calls.last.request.headers["authorization"] == f"Bearer {VENICE}"
    assert meter.as_dict()["by_model"]["jev-latest"] == {"calls": 2, "tokens_in": 200, "tokens_out": 10}


@respx.mock
async def test_jev_quota_and_missing_key(services) -> None:  # type: ignore[no-untyped-def]
    respx.post(DECISIONS).mock(return_value=httpx.Response(402, json={}))
    client = JevClient(Settings(HUSK_ENV="test", VENICE_API_KEY=VENICE), UserAiKeyStore(services.db))
    with pytest.raises(DecisionUnavailable) as e:
        await client.ask(USER, ORG, "s", {"x": Question(type="noul", instructions="?")})
    assert e.value.key == "errors.decisions_quota"
    with pytest.raises(DecisionUnavailable):
        await JevClient(Settings(HUSK_ENV="test", VENICE_API_KEY=None), UserAiKeyStore(services.db)).ask(USER, ORG, "s", {})


def test_hours_and_score_conversion() -> None:
    hours, spread = q.expected_hours_per_week({"3": 1.0})  # 5–10 h/month → midpoint 7.5
    assert hours == round(7.5 / 4.33, 1) and spread == 0
    assert q.score_value({"8": 1.0}) == 0.85


# --- keys -----------------------------------------------------------------------------------------
def test_combined_key_split() -> None:
    assert split_keys(f"sk-regolo-123{VENICE}") == ("sk-regolo-123", VENICE)
    assert split_keys(f" sk-regolo-123 \n {VENICE} ") == ("sk-regolo-123", VENICE)
    assert split_keys("sk-regolo-only") == ("sk-regolo-only", None)
    assert split_keys(VENICE) == ("", VENICE)


@respx.mock
def test_key_screen_verifies_both_parts(settings, services) -> None:  # type: ignore[no-untyped-def]
    from app.endpoints.deps import SESSION_COOKIE
    from app.gateways.regolo.provider import RegoloProvider
    from app.main import create_app
    from test.conftest import login

    services.llm = RegoloProvider(settings, UsageRepo(services.db), services.ai_keys)
    services.decisions = JevClient(settings, services.ai_keys, UsageRepo(services.db))
    respx.get("https://api.regolo.ai/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "gpt-oss-120b"}]}))
    venice = respx.get("https://api.venice.ai/api/v1/models").mock(return_value=httpx.Response(401))
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        c.cookies.set(SESSION_COOKIE, login(services))
        r = c.put("/api/v1/user/ai-key", json={"api_key": "sk-regolo-12345678"})
        assert r.json()["code"] == "errors.ai_key_venice_missing"
        r = c.put("/api/v1/user/ai-key", json={"api_key": f"sk-regolo-12345678{VENICE}"})
        assert r.json()["code"] == "errors.ai_key_venice_rejected" and services.ai_keys.get(USER) is None
        venice.mock(return_value=httpx.Response(200, json={"data": []}))
        r = c.put("/api/v1/user/ai-key", json={"api_key": f"sk-regolo-12345678{VENICE}"})
        body = r.json()
        assert r.status_code == 200 and body["configured"] and body["decision_hint"] == mask(VENICE)
        assert VENICE not in r.text
        stored = services.ai_keys.get(USER)
        assert stored.api_key == "sk-regolo-12345678" and stored.decision_key == VENICE
        c.post("/api/v1/user/auth/logout")
        assert services.ai_keys.get(USER) is None  # both deleted on sign-out


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


def test_discovery_requires_the_venice_key(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    from test.fakes import FakeDecisionModel

    services.decisions = FakeDecisionModel(configured=False)
    upload_samples(client)
    r = client.post("/api/v1/discovery/runs")
    assert r.status_code == 503 and r.json()["code"] == "errors.ai_key_venice_missing"


def test_model_constraint_violations_are_note_errors_not_crashes(services) -> None:  # type: ignore[no-untyped-def]
    from app.core.knowledge.memory.notes import NoteInvalid

    mem = services.memory(ORG)
    net = mem.networks.create("run_t", "Rete", [])
    with pytest.raises(NoteInvalid):
        mem.networks.write_note(net.id, title="x", claim="troppo corto", frequency="ogni giorno")
