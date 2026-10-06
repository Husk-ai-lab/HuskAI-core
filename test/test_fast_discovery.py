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
            {"ref": "c", "kind": "notification", "label": "Avvisa", "after": "c", "channel": "desktop"},
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


def test_parallel_work_is_reported_as_counters_not_names(client, services) -> None:  # type: ignore[no-untyped-def]
    """Summaries and drafts run in parallel: the UI gets "n of total", never one line per file or title."""
    from test.conftest import upload_samples
    from test.test_discovery_e2e import wait_run

    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    assert wait_run(client, run["id"])["run"]["status"] == "completed"
    events = list(services.events._backlog[f"discovery:{run['id']}"])
    reading = [e for e in events if e["key"] == "discovery.event.reading"]
    drafting = [e for e in events if e["key"] == "discovery.event.drafting"]
    assert reading and drafting
    for e in reading + drafting:
        assert set(e["params"]) == {"done", "total"} and e["params"]["done"] <= e["params"]["total"]
    assert reading[-1]["params"]["done"] == reading[-1]["params"]["total"]
    assert drafting[-1]["params"]["done"] == drafting[-1]["params"]["total"]
    assert not any(e["key"] in ("discovery.event.analyzing",) for e in events)


def test_the_reading_counter_only_goes_up(client, services) -> None:  # type: ignore[no-untyped-def]
    """The user sees one number that grows: events are increasing and `done` never decreases."""
    from test.conftest import upload_samples
    from test.test_discovery_e2e import wait_run

    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    assert wait_run(client, run["id"])["run"]["status"] == "completed"
    events = list(services.events._backlog[f"discovery:{run['id']}"])
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events) and len({e["seq"] for e in events}) == len(events)
    for key in ("discovery.event.reading", "discovery.event.drafting"):
        done = [e["params"]["done"] for e in events if e["key"] == key]
        assert done == sorted(done), (key, done)
    progress = [e["progress"] for e in events if e["type"] == "progress"]
    assert progress == sorted(progress)


def test_event_hub_replays_only_what_a_client_missed_and_forgets_old_attempts() -> None:
    from app.core.events import EventHub

    hub = EventHub()
    for i in range(5):
        hub.publish("c", {"type": "progress", "n": i})
    q = hub.subscribe("c", after=3)
    assert [q.get_nowait()["seq"] for _ in range(q.qsize())] == [4, 5]
    hub.publish("c", {"type": "failed"})
    hub.clear("c")  # a new attempt starts
    hub.publish("c", {"type": "progress"})
    fresh = hub.subscribe("c")
    assert fresh.qsize() == 1 and fresh.get_nowait()["seq"] == 7  # no stale "failed", ids keep increasing


def test_a_failed_attempt_leaves_no_task_counting_in_the_background(client, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """One document fails while others are still being read: the others stop with it, so a retry's counter is the only one."""
    import asyncio

    from test.conftest import upload_samples
    from test.test_discovery_e2e import wait_run

    original = Summarizer.card_for

    async def flaky(self, doc):  # type: ignore[no-untyped-def]
        if doc.filename.startswith("Procedura"):
            raise RuntimeError("boom")
        await asyncio.sleep(0.4)
        return await original(self, doc)

    monkeypatch.setattr(Summarizer, "card_for", flaky)
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    assert wait_run(client, run["id"])["run"]["status"] == "failed"
    channel = f"discovery:{run['id']}"
    failed_at = next(e["seq"] for e in services.events._backlog[channel] if e["type"] == "failed")
    time.sleep(0.8)  # long enough for the slow documents to finish, had they been left running
    late = [e for e in services.events._backlog[channel] if e["seq"] > failed_at and e["key"] == "discovery.event.reading"]
    assert late == []
