"""Full discovery through the API with the scripted LLM: upload → 7 steps → map → memory."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from test.conftest import upload_samples


def wait_run(client: TestClient, run_id: str, timeout: float = 30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        v = client.get(f"/api/v1/discovery/runs/{run_id}").json()
        if v["run"]["status"] in ("completed", "failed", "paused"):
            return v
        time.sleep(0.1)
    raise AssertionError("discovery did not finish")


def test_discovery_end_to_end(client: TestClient) -> None:
    assert client.get("/api/v1/discovery/state").json()["state"] == "da_fare"
    docs = upload_samples(client)
    assert {d["status"] for d in docs} == {"ingested"}

    run = client.post("/api/v1/discovery/runs").json()["run"]
    view = wait_run(client, run["id"])
    assert view["run"]["status"] == "completed", view
    assert all(s["status"] == "done" for s in view["steps"])
    assert len(view["steps"]) == 7

    report = client.get(f"/api/v1/discovery/runs/{run['id']}/report").json()
    assert report["documents_analyzed"] == 3
    assert report["opportunities"] >= 1
    assert report["total_hours_per_week"] > 0
    assert report["repetitive_activities"] >= 1

    autos = client.get("/api/v1/automations").json()
    assert len(autos) == report["opportunities"]
    a = autos[0]
    assert a["status"] == "bozza"
    kinds = {n["kind"] for n in a["graph"]["nodes"]}
    assert {"trigger", "agent", "condition", "action"} <= kinds

    # harness memory: company profile + one kept network (with summary) per automation
    mem = client.get("/api/v1/discovery/memory").json()
    assert 1 <= len(mem["facts"]) <= 30
    assert mem["company_profile_markdown"].startswith("# Company profile")
    assert len(mem["networks"]) == len(autos)
    assert all(n["summary_markdown"] for n in mem["networks"])
    amem = client.get(f"/api/v1/automations/{a['id']}/memory").json()
    assert "Knowledge network" in amem["notes_markdown"]

    st = client.get("/api/v1/discovery/state").json()
    assert st["state"] == "fatta"

    # documents now report which automation uses them
    used = [d for d in client.get("/api/v1/documents").json() if d["used_by"]]
    assert used


def test_discovery_requires_documents(client: TestClient) -> None:
    r = client.post("/api/v1/discovery/runs")
    assert r.status_code == 400


def test_discovery_resume_after_failure(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    from test.fakes import FakeProvider, default_handler

    upload_samples(client)
    state = {"fail": True}

    def flaky(messages, tools):  # type: ignore[no-untyped-def]
        if tools == ["OpportunityMap"] and state["fail"]:
            raise RuntimeError("modello non disponibile")
        return default_handler(messages, tools)

    services.llm = FakeProvider(flaky)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    view = wait_run(client, run["id"])
    assert view["run"]["status"] == "failed"
    done_before = [s["step"] for s in view["steps"] if s["status"] == "done"]
    assert done_before == ["load", "knowledge_base", "extract", "repetitive", "handoffs"]

    state["fail"] = False
    calls_before = services.llm.calls
    client.post(f"/api/v1/discovery/runs/{run['id']}/resume")
    view = wait_run(client, run["id"])
    assert view["run"]["status"] == "completed"
    # resumed from the failed step: extraction/research were not paid for again
    assert services.llm.calls - calls_before < 10
