"""Summary-based discovery: document cards (step 3) and the confidence-driven Explorer (step 4)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.core.agents.explorer.summarizer import Summarizer
from app.data.models.knowledge import CardKind
from app.gateways.sqlite.repositories import CardRepo
from test.conftest import ORG, PROCEDURE_MD, USER, production_csv, upload_samples
from test.fakes import FakeProvider, default_handler
from test.test_discovery_e2e import wait_run


def _add(services, name: str, content: bytes):  # type: ignore[no-untyped-def]
    return services.knowledge(ORG).add(name, content, USER, True)


# --- cards ------------------------------------------------------------------------------------------
async def test_spreadsheet_card_is_computed_without_ai(services) -> None:  # type: ignore[no-untyped-def]
    doc = _add(services, "Report_Produzione.csv", production_csv())
    before = services.llm.calls
    card, built = await Summarizer(services.agent_context(ORG, USER, language="en")).card_for(doc)
    assert built and card.kind == CardKind.table and card.model is None
    assert services.llm.calls == before  # no model call at all
    metrics = {s.metric: s for s in card.stats}
    assert metrics["rows"].value["rows"] == 14
    assert metrics["cadence"].value["cadence"] == "daily" and "every day" in metrics["cadence"].text
    samples = [s for s in card.stats if s.metric == "sample_row"]
    assert len(samples) == 3 and samples[0].cells == "csv!A2:E2"
    assert all(s.id == f"S{i + 1}" for i, s in enumerate(card.stats))
    assert "14 rows" in card.sentence and "every day" in card.sentence
    ev = card.evidence_for(metrics["cadence"].id)[0]
    assert ev.stat_id == metrics["cadence"].id and ev.document_id == doc.id


async def test_text_card_has_sentence_summary_signals_and_real_quotes(services) -> None:  # type: ignore[no-untyped-def]
    doc = _add(services, "Procedura_Accettazione.md", PROCEDURE_MD.encode())
    card, _ = await Summarizer(services.agent_context(ORG, USER, language="en")).card_for(doc)
    assert card.kind == CardKind.text and card.model == "scripted-summary-model"
    assert card.sentence and card.summary and card.signals["manual"] is True and card.signals["dept"] == ["Produzione"]
    assert len(card.quotes) == 1 and card.quotes[0].chunk_id.startswith(doc.id)  # the invented passage was dropped
    assert card.quotes[0].quote.lower() in PROCEDURE_MD.lower()
    line = card.line("D01")
    assert line.startswith("D01 | ") and "manual=yes" in line and "{" not in line  # compact, not JSON
    assert services.memory(ORG).repo.facts()


async def test_cards_are_cached_by_file_and_version(services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    doc = _add(services, "Procedura.md", PROCEDURE_MD.encode())
    s = Summarizer(services.agent_context(ORG, USER))
    await s.card_for(doc)
    calls = services.llm.calls
    card, built = await s.card_for(doc)
    assert not built and services.llm.calls == calls  # cached: free
    import app.core.agents.explorer.summarizer as summ

    monkeypatch.setattr(summ, "CARD_VERSION", 99)
    _, built = await summ.Summarizer(services.agent_context(ORG, USER)).card_for(doc)
    assert built and services.llm.calls == calls + 1  # format changed: rebuilt
    assert CardRepo(services.db, "org_other").get(doc.id) is None  # org-scoped


async def test_summary_model_falls_back_to_main(services) -> None:  # type: ignore[no-untyped-def]
    services.llm = FakeProvider(summary_model=None)  # type: ignore[assignment]
    doc = _add(services, "Procedura.md", PROCEDURE_MD.encode())
    card, _ = await Summarizer(services.agent_context(ORG, USER)).card_for(doc)
    assert services.llm.models_used == ["scripted-test-model"] and card.model == "scripted-test-model"


# --- the pipeline -------------------------------------------------------------------------------------
def test_discovery_pipeline_is_cheap_and_complete(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    seen: list[tuple[list[str], str]] = []

    def recording(messages, tools):  # type: ignore[no-untyped-def]
        seen.append((list(tools), "\n".join(str(m.content) for m in messages)))
        return default_handler(messages, tools)

    services.llm.handler = recording
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    view = wait_run(client, run["id"])
    assert view["run"]["status"] == "completed", view
    assert services.llm.calls <= 12  # 1 card + 1 map + 1 triage + briefs + drafts/summaries
    schemas = [t[0] for t, _ in seen if len(t) == 1]
    assert schemas.count("CardDraft") == 1  # only the text document; the spreadsheets are computed
    assert "ExplorerMap" not in schemas and "Triage" not in schemas  # linking/assessment: the decision model
    calls = services.decisions.calls
    assert calls and "D01 | " in calls[0]["state"] and '"sentence"' not in calls[0]["state"]  # card lines, not JSON
    draft_prompt = next(p for t, p in seen if t == ["FlowDraft"])
    assert "Brief from the Explorer" in draft_prompt
    assert client.get("/api/v1/automations").json() and len(CardRepo(services.db, ORG).list()) == 3
    # efforts: low across discovery, medium only for the Builder
    efforts = dict(zip(services.llm.models_used, services.llm.efforts, strict=True))
    assert "high" not in services.llm.efforts and efforts


async def test_cards_are_built_concurrently_within_the_limit(services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.agents import orchestrator as orch_mod

    live = {"now": 0, "max": 0}
    original = Summarizer.card_for

    async def slow(self, doc):  # type: ignore[no-untyped-def]
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        await asyncio.sleep(0.02)
        try:
            return await original(self, doc)
        finally:
            live["now"] -= 1

    monkeypatch.setattr(orch_mod.Summarizer, "card_for", slow)
    services.settings.discovery_concurrency = 2
    for i in range(5):
        _add(services, f"Proc_{i}.md", (PROCEDURE_MD + f"\nVersione {i}.").encode())
    o = orch_mod.DiscoveryOrchestrator(services.agent_context(ORG, USER), services.events)
    run = o.create_run(USER)
    await o.execute(run.id)
    assert live["max"] == 2


async def test_a_failing_document_gets_a_basic_card_and_never_fails_the_step(services) -> None:  # type: ignore[no-untyped-def]
    def broken_cards(messages, tools):  # type: ignore[no-untyped-def]
        if tools == ["CardDraft"] or "JSON Schema" in str(messages[-1].content):
            raise ValueError("malformed JSON from the model")
        return default_handler(messages, tools)

    services.llm = FakeProvider(broken_cards)  # type: ignore[assignment]
    doc = _add(services, "Procedura.md", PROCEDURE_MD.encode())
    s = Summarizer(services.agent_context(ORG, USER))
    card, _ = await s.card_for(doc)
    assert card.model == "fallback" and card.version == 0 and card.title and card.sentence
    assert services.llm.models_used == ["scripted-summary-model", "scripted-test-model"]  # summary, then main
    assert CardRepo(services.db, ORG).fresh(doc.id, doc.sha256, 1) is None  # retried on the next run
