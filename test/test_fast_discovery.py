"""Fast discovery: resilient parallel Builders, loop repair, reasoning policy, summary deadline."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.agents.builder.compiler import compile_draft, repair_order
from app.core.agents.builder.schemas import FlowDraft
from app.core.agents.explorer.summarizer import Summarizer
from app.helpers.settings import Settings
from test.conftest import ORG, PROCEDURE_MD, USER, upload_samples
from test.fakes import FakeProvider, default_handler
from test.test_discovery_e2e import wait_run


def test_loops_are_repaired_without_asking_the_model() -> None:
    draft = FlowDraft.model_validate({
        "title": "T", "description": "D", "trigger": {"type": "manual", "label": "Avvio"},
        "steps": [
            {"ref": "a", "kind": "agent", "label": "Analisi", "after": "b", "instructions": "x"},
            {"ref": "b", "kind": "condition", "label": "Anomalie?", "after": "a", "field": "n", "op": ">", "value": 0},
            {"ref": "c", "kind": "action", "label": "Avvisa", "after": "c", "action": "notify"},
        ]})
    with pytest.raises(ValueError):
        compile_draft(draft)
    graph = compile_draft(repair_order(draft))
    assert {e.source for e in graph.edges} <= {"trigger", "a", "b"}
    assert any(e.branch == "vero" for e in graph.edges if e.source == "b")  # re-attached under a condition


def test_one_failing_draft_never_fails_the_run(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    def broken_first_draft(messages, tools):  # type: ignore[no-untyped-def]
        if tools == ["FlowDraft"] and "Report giornaliero automatico" in "\n".join(str(m.content) for m in messages):
            raise RuntimeError("draft failed")
        return default_handler(messages, tools)

    services.llm.handler = broken_first_draft
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    view = wait_run(client, run["id"])
    assert view["run"]["status"] == "completed"
    report = client.get(f"/api/v1/discovery/runs/{run['id']}/report").json()
    assert report["opportunities"] > len(client.get("/api/v1/automations").json())  # one skipped, kept as opportunity


def test_reasoning_policy() -> None:
    with pytest.raises(ValidationError):
        Settings(HUSK_REASONING_BUILDER="high")
    s = Settings()
    assert (s.reasoning_summary, s.reasoning_explorer, s.reasoning_planner) == ("low", "low", "low")
    assert s.reasoning_builder == "medium" and s.reasoning_runtime == "medium"


def test_roles_send_the_right_effort(services) -> None:  # type: ignore[no-untyped-def]
    ctx = services.agent_context(ORG, USER)
    for role in ("summary", "explorer", "planner", "builder", "runtime"):
        ctx.model(role=role)
    assert services.llm.efforts == ["low", "low", "low", "medium", "medium"]
    assert services.llm.models_used[0] == "scripted-summary-model"


async def test_slow_document_gets_a_basic_card_within_the_deadline(services) -> None:  # type: ignore[no-untyped-def]
    def slow(messages, tools):  # type: ignore[no-untyped-def]
        if tools == ["CardDraft"]:
            time.sleep(1.0)
        return default_handler(messages, tools)

    services.llm = FakeProvider(slow)  # type: ignore[assignment]
    services.settings.summary_deadline_s = 0.3
    doc = services.knowledge(ORG).add("Lento.md", PROCEDURE_MD.encode(), USER, True)
    started = time.perf_counter()
    card, _ = await Summarizer(services.agent_context(ORG, USER)).card_for(doc)
    assert time.perf_counter() - started < 0.9
    assert card.model == "fallback" and card.version == 0
