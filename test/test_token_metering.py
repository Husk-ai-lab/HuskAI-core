"""Token metering per step and per model, the budget guard, and the discovery benchmark (offline)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from app.core.metering import BudgetExceeded, Meter, metered
from test.conftest import upload_samples
from test.fakes import FakeProvider
from test.test_discovery_e2e import wait_run


def test_discovery_usage_is_metered_per_step_and_model(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    assert wait_run(client, run["id"])["run"]["status"] == "completed"
    usage = client.get(f"/api/v1/discovery/runs/{run['id']}/report").json()["usage"]
    steps = usage["steps"]
    # step 3: one text document on the summary model; the two spreadsheets cost nothing
    assert steps["extract"]["by_model"] == {"scripted-summary-model": {"calls": 1, "tokens_in": 100, "tokens_out": 20}}
    assert set(steps["repetitive"]["by_model"]) == {"jev-fake"}  # linking: decision model only, no LLM
    assert steps["load"]["total"]["calls"] == 0 and steps["knowledge_base"]["total"]["calls"] == 0
    # per-step numbers add up to the run total, which matches every call the provider made
    llm_calls = sum(m["calls"] for k, m in usage["by_model"].items() if k != "jev-fake")
    assert sum(s["total"]["calls"] for s in steps.values()) == usage["total"]["calls"]
    assert llm_calls == services.llm.calls and usage["by_model"]["jev-fake"]["calls"] == len(services.decisions.calls)
    assert usage["by_model"]["scripted-summary-model"]["calls"] == 1


def test_summary_calls_fall_back_to_the_main_model_id(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    services.llm = FakeProvider(summary_model=None)
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    wait_run(client, run["id"])
    usage = client.get(f"/api/v1/discovery/runs/{run['id']}/report").json()["usage"]
    assert set(usage["steps"]["extract"]["by_model"]) == {"scripted-test-model"}


async def test_budget_stops_calls_before_they_are_sent() -> None:
    llm = FakeProvider()
    meter = Meter(budget=100)  # each scripted call reports 120 tokens: the check runs before each call
    with metered(meter):
        model = llm.chat_model()
        await model.ainvoke([HumanMessage(content="ciao")])
        with pytest.raises(BudgetExceeded):
            await llm.chat_model().ainvoke([HumanMessage(content="ancora")])
    assert meter.calls == 1 and meter.tokens == 120


async def test_nested_meters_both_count() -> None:
    llm = FakeProvider()
    outer, inner = Meter(), Meter()
    with metered(outer):
        with metered(inner):
            await llm.chat_model(model="m-small").ainvoke([HumanMessage(content="x")])
        await llm.chat_model().ainvoke([HumanMessage(content="y")])
    assert inner.as_dict()["by_model"] == {"m-small": {"calls": 1, "tokens_in": 100, "tokens_out": 20}}
    assert outer.calls == 2 and set(outer.by_model) == {"m-small", "scripted-test-model"}


async def test_offline_benchmark_on_a_subset() -> None:
    from benchmarks.bench_discovery_tokens import fmt_report, run

    r = await run(live=False, provider="openrouter", label="ci", budget=None, subset=5)
    assert r["completed"], r["error"]
    assert r["documents"] == 5
    assert r["total"]["calls"] <= 15  # guard: no regression to loop-style costs
    assert {s["step"] for s in r["steps"]} >= {"extract", "repetitive", "map"}
    assert all("by_model" in s for s in r["steps"])
    assert "Discovery benchmark" in fmt_report([r])
