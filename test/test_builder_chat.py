"""Assistente Husk: edits through typed tools, personalities, proposals, undo."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from test.conftest import upload_samples
from test.test_discovery_e2e import wait_run


def _discovered(client: TestClient) -> dict:
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    assert wait_run(client, run["id"])["run"]["status"] == "completed"
    return client.get("/api/v1/automations").json()[0]


def _send(client: TestClient, chat_id: str, content: str, **kw) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    events = []
    with client.stream("POST", f"/api/v1/agent/chats/{chat_id}/messages", json={"content": content, **kw}) as r:
        assert r.status_code == 200, r.read()
        name = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and name:
                events.append((name, json.loads(line.split(":", 1)[1].strip() or "{}")))
    return events


def test_chat_edits_flow_and_versions(client: TestClient) -> None:
    a = _discovered(client)
    view = client.get(f"/api/v1/agent/chats/automation/{a['id']}").json()
    assert view["messages"][0]["role"] == "assistant"
    assert view["suggestions"]
    events = _send(client, view["chat"]["id"], "Avvisa anche gli Acquisti")
    names = [e[0] for e in events]
    assert "patch" in names and names[-1] == "done"
    patched = next(d for n, d in events if n == "patch")["automation"]
    labels = [n["label"] for n in patched["graph"]["nodes"]]
    assert "Avvisa Acquisti" in labels
    assert patched["version"] == 2
    # undo restores the previous version
    undone = client.post(f"/api/v1/automations/{a['id']}/undo").json()
    assert "Avvisa Acquisti" not in [n["label"] for n in undone["graph"]["nodes"]]


def test_ultra_careful_personality_proposes(client: TestClient) -> None:
    a = _discovered(client)
    chat = client.get(f"/api/v1/agent/chats/automation/{a['id']}").json()["chat"]
    client.patch(f"/api/v1/agent/chats/{chat['id']}", json={"personality": "ultra_prudente"})
    events = _send(client, chat["id"], "Alza la soglia delle anomalie")
    names = [e[0] for e in events]
    assert "proposal" in names and "patch" not in names
    prop = next(d for n, d in events if n == "proposal")
    # nothing changed until the user accepts
    assert client.get(f"/api/v1/automations/{a['id']}").json()["automation"]["version"] == 1
    r = client.post(f"/api/v1/agent/proposals/{prop['id']}", json={"accept": True}).json()
    cond = next(n for n in r["automation"]["graph"]["nodes"] if n["id"] == "anomalie")
    assert cond["value"] == 3
    # a decided proposal cannot be applied twice
    assert client.post(f"/api/v1/agent/proposals/{prop['id']}", json={"accept": True}).status_code == 404


def test_personalities_listed(client: TestClient) -> None:
    ids = {p["id"] for p in client.get("/api/v1/agent/personalities").json()}
    assert ids == {"bilanciato", "ultra_prudente", "esplicativo", "conciso"}
