"""Suggestions after document changes: debounce, evidence checks, deletes, and the Builder's
stricter handling of suggestion messages (always a proposal, never applied directly)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.data.models.suggestion import Suggestion, SuggestionKind
from app.endpoints.deps import SESSION_COOKIE
from app.gateways.sqlite.repositories import SuggestionRepo
from test.conftest import ORG, login
from test.test_builder_chat import _discovered, _send

NEW_PROCEDURE = """# Procedura anomalie distinta base (rev. 2)

## Nuova regola
Da ottobre, a ogni anomalia nella distinta base bisogna avvisare gli Acquisti oltre all'Ufficio tecnico.
Il controllo viene fatto a mano ogni sera e ricopiato nel gestionale.
"""


def _fast(services, debounce: float = 0.05) -> None:  # type: ignore[no-untyped-def]
    services.suggestions.cancel_all()  # forget jobs queued by earlier uploads
    services.suggestions.debounce_s = debounce


def _wait_idle(client: TestClient, timeout: float = 30) -> None:
    time.sleep(0.1)
    end = time.time() + timeout
    while time.time() < end:
        if not client.get("/api/v1/suggestions/summary").json()["processing"]:
            return
        time.sleep(0.05)
    raise AssertionError("suggestion job did not finish")


def _upload_new(client: TestClient, name: str = "Procedura_Anomalie_Rev2.md", text: str = NEW_PROCEDURE) -> dict:
    r = client.post("/api/v1/documents", files=[("files", (name, text.encode(), "text/markdown"))],
                    data={"authorized": "true"})
    assert r.status_code == 200, r.text
    return r.json()["documents"][0]


def _by_kind(client: TestClient, kind: str, status: str = "open") -> list[dict]:
    return [s for s in client.get(f"/api/v1/suggestions?status={status}").json() if s["kind"] == kind]


def test_before_discovery_only_suggests_running_it(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    _fast(services)
    _upload_new(client)
    _wait_idle(client)
    _upload_new(client, "Altro.md", "# Altro\nUn altro documento.")
    _wait_idle(client)
    items = client.get("/api/v1/suggestions").json()
    assert [s["kind"] for s in items] == ["run_discovery"]  # once, deduplicated, and no LLM call
    assert client.get("/api/v1/suggestions/summary").json()["unread"] == 1


def test_new_document_suggests_change_with_checked_evidence(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    a = _discovered(client)
    _fast(services)
    doc = _upload_new(client)
    _wait_idle(client)
    mods = _by_kind(client, "modify_automation")
    assert len(mods) == 1
    s = mods[0]
    assert s["automation_id"] == a["id"] and s["document_ids"] == [doc["id"]]
    assert all(e["chunk_id"].startswith(doc["id"]) for e in s["evidence"])
    assert s["prompt"] and s["status"] == "new"
    # the invented-evidence and low-confidence drafts were dropped
    assert not _by_kind(client, "new_automation")
    summary = client.get("/api/v1/suggestions/summary").json()
    assert summary["unread"] >= 1 and summary["processing"] is False
    ids = [x["id"] for x in client.get("/api/v1/suggestions").json()]
    client.post("/api/v1/suggestions/seen", json={"ids": ids})
    assert client.get("/api/v1/suggestions/summary").json()["unread"] == 0
    # uploading the same file again creates no duplicate
    _upload_new(client)
    _wait_idle(client)
    assert len(_by_kind(client, "modify_automation")) == 1


def test_profile_change_creates_memory_update(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    from langchain_core.messages import AIMessage

    _discovered(client)
    _fast(services)
    base = services.llm.handler

    def new_fact(messages, tools):  # type: ignore[no-untyped-def]
        msg = base(messages, tools)
        if tools == ["DocumentAnalysis"] and "Anomalie_Rev2" in "\n".join(str(m.content) for m in messages):
            call = dict(msg.tool_calls[0])
            call["args"] = {**call["args"], "facts": [{"text": "Da ottobre 2026 le anomalie vanno segnalate agli Acquisti.",
                                                     "category": "processo", "importance": 0.8, "quote": "Acquisti"}]}
            return AIMessage(content="", tool_calls=[call])
        return msg

    services.llm.handler = new_fact
    _upload_new(client)
    _wait_idle(client)
    mem = _by_kind(client, "memory_update")
    assert len(mem) == 1 and "Acquisti" in mem[0]["body"] and mem[0]["body"].startswith("+ ")
    assert client.post(f"/api/v1/suggestions/{mem[0]['id']}/apply").json()["suggestion"]["status"] == "applied"


def test_suggestion_message_always_becomes_a_proposal(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    a = _discovered(client)
    _fast(services)
    doc = _upload_new(client)
    _wait_idle(client)
    s = _by_kind(client, "modify_automation")[0]

    seen: list[str] = []
    base = services.llm.handler

    def recording(messages, tools):  # type: ignore[no-untyped-def]
        seen.append("\n".join(str(m.content) for m in messages))
        return base(messages, tools)

    services.llm.handler = recording
    chat = client.get(f"/api/v1/agent/chats/automation/{a['id']}").json()["chat"]
    assert chat["personality"] == "bilanciato"  # this personality normally applies changes directly
    events = _send(client, chat["id"], s["prompt"], suggestion_id=s["id"])
    names = [e[0] for e in events]
    assert "proposal" in names and "patch" not in names
    user = next(d for n, d in events if n == "user")
    assert user["meta"]["origin"] == "suggestion" and user["meta"]["suggestion_id"] == s["id"]
    assert any("HUSK SUGGESTION" in p and "NOT written by the user" in p for p in seen)
    assert client.get(f"/api/v1/automations/{a['id']}").json()["automation"]["version"] == 1

    prop = next(d for n, d in events if n == "proposal")
    assert prop["suggestion_id"] == s["id"]
    client.post(f"/api/v1/agent/proposals/{prop['id']}", json={"accept": True})
    assert client.get(f"/api/v1/suggestions/{s['id']}").json()["status"] == "applied"
    # the new document joins the automation's notes in the background
    end = time.time() + 20
    while time.time() < end:
        auto = client.get(f"/api/v1/automations/{a['id']}").json()["automation"]
        if doc["id"] in auto["source_document_ids"]:
            break
        time.sleep(0.1)
    assert doc["id"] in auto["source_document_ids"]
    # history replays the suggestion as tagged, so it is never mistaken for user intent
    services.llm.handler = recording
    seen.clear()
    _send(client, chat["id"], "Grazie")
    assert any("[Husk suggestion" in p for p in seen)


def test_edited_suggestion_is_the_users_own_request(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    a = _discovered(client)
    _fast(services)
    _upload_new(client)
    _wait_idle(client)
    s = _by_kind(client, "modify_automation")[0]
    chat = client.get(f"/api/v1/agent/chats/automation/{a['id']}").json()["chat"]
    events = _send(client, chat["id"], "Avvisa anche gli Acquisti, per favore", suggestion_id=s["id"])
    user = next(d for n, d in events if n == "user")
    assert "origin" not in user["meta"] and user["meta"]["suggestion_id"] == s["id"]
    assert "patch" in [e[0] for e in events]  # normal personality behaviour


def test_discarding_the_proposal_dismisses_the_suggestion(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    a = _discovered(client)
    _fast(services)
    _upload_new(client)
    _wait_idle(client)
    s = _by_kind(client, "modify_automation")[0]
    chat = client.get(f"/api/v1/agent/chats/automation/{a['id']}").json()["chat"]
    prop = next(d for n, d in _send(client, chat["id"], s["prompt"], suggestion_id=s["id"]) if n == "proposal")
    client.post(f"/api/v1/agent/proposals/{prop['id']}", json={"accept": False})
    assert client.get(f"/api/v1/suggestions/{s['id']}").json()["status"] == "dismissed"


def test_suggestion_of_another_automation_is_refused(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    a = _discovered(client)
    chat = client.get(f"/api/v1/agent/chats/automation/{a['id']}").json()["chat"]
    r = client.post(f"/api/v1/agent/chats/{chat['id']}/messages", json={"content": "x", "suggestion_id": "sug_nope"})
    assert r.status_code == 404 and r.json()["code"] == "errors.suggestion_not_found"


def test_deleting_a_used_document_suggests_a_review(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    a = _discovered(client)
    used = a["source_document_ids"][0]
    name = next(d["filename"] for d in client.get("/api/v1/documents").json() if d["id"] == used)
    assert client.delete(f"/api/v1/documents/{used}").status_code == 200
    items = _by_kind(client, "source_deleted")
    assert len(items) == 1
    s = items[0]
    assert s["automation_id"] == a["id"] and name in s["prompt"]
    # changes to an existing automation never happen from the Suggestions page
    r = client.post(f"/api/v1/suggestions/{s['id']}/apply")
    assert r.status_code == 400 and r.json()["code"] == "errors.suggestion_use_chat"
    client.post(f"/api/v1/suggestions/{s['id']}/dismiss")
    assert not _by_kind(client, "source_deleted")
    assert _by_kind(client, "source_deleted", "dismissed")


def test_new_automation_suggestion_creates_a_draft(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    _discovered(client)
    doc = _upload_new(client)
    services.suggestions.cancel_all()
    s = SuggestionRepo(services.db, ORG).save(Suggestion(
        id="sug_test_new", org_id=ORG, kind=SuggestionKind.new_automation, title="Controllo anomalie serale",
        body="Il controllo serale è manuale.", document_ids=[doc["id"]], hours_per_week=2))
    r = client.post(f"/api/v1/suggestions/{s.id}/apply")
    assert r.status_code == 200, r.text
    auto_id = r.json()["automation_id"]
    auto = client.get(f"/api/v1/automations/{auto_id}").json()["automation"]
    assert auto["status"] == "bozza" and auto["run_id"] is None  # "Analyze again" won't retire it
    assert r.json()["suggestion"]["status"] == "applied"
    assert client.post(f"/api/v1/suggestions/{s.id}/apply").status_code == 409
    mem = client.get(f"/api/v1/automations/{auto_id}/memory").json()
    assert mem["summary_markdown"]


def test_upload_batches_are_debounced_into_one_job(client: TestClient, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.suggestions import engine

    calls: list[list[str]] = []

    async def fake_process(self, document_ids):  # type: ignore[no-untyped-def]
        calls.append(list(document_ids))
        return []

    monkeypatch.setattr(engine.SuggestionEngine, "process", fake_process)
    _fast(services, debounce=0.4)
    for i in range(3):
        _upload_new(client, f"Doc_{i}.md", f"# Documento {i}\nTesto diverso {i}.")
    _wait_idle(client)
    assert len(calls) == 1 and len(calls[0]) == 3


def test_no_ai_key_means_no_analysis(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    _discovered(client)
    _fast(services)
    services.llm.configured_for = lambda _uid: False
    before = services.llm.calls
    _upload_new(client)
    _wait_idle(client)
    assert services.llm.calls == before
    assert not _by_kind(client, "modify_automation")


def test_suggestions_are_org_scoped(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    SuggestionRepo(services.db, ORG).save(Suggestion(id="sug_org", org_id=ORG, kind=SuggestionKind.run_discovery,
                                                     title="x"))
    client.cookies.set(SESSION_COOKIE, login(services, user_id="user_other", org_id="org_other"))
    assert client.get("/api/v1/suggestions?status=all").json() == []
    assert client.get("/api/v1/suggestions/sug_org").status_code == 404
    assert client.post("/api/v1/suggestions/sug_org/dismiss").status_code == 404


def test_folder_upload_keeps_safe_relative_paths(client: TestClient) -> None:
    files = [("files", ("a.md", b"# A\nuno", "text/markdown")), ("files", ("b.md", b"# B\ndue", "text/markdown"))]
    r = client.post("/api/v1/documents", files=files,
                    data={"authorized": "true", "paths": ["Qualita/Procedure/a.md", "../../etc/b.md"]})
    assert r.status_code == 200, r.text
    paths = {d["filename"]: d["source_path"] for d in r.json()["documents"]}
    assert paths == {"a.md": "Qualita/Procedure/a.md", "b.md": "etc/b.md"}
    listed = {d["filename"]: d["source_path"] for d in client.get("/api/v1/documents").json()}
    assert listed["a.md"] == "Qualita/Procedure/a.md"
    assert client.get("/api/v1/suggestions?status=bogus").status_code == 400


def test_documents_covered_by_discovery_are_not_reanalysed(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    _discovered(client)
    _fast(services)
    before = services.llm.calls
    # the upload job of the documents discovery just analysed runs late: it must find nothing to do
    ids = [d["id"] for d in client.get("/api/v1/documents").json()]
    client.portal.call(services.suggestions.enqueue, ORG, "user_test", ids)  # type: ignore[union-attr]
    _wait_idle(client)
    assert services.llm.calls == before
    assert client.get("/api/v1/suggestions?status=all").json() == []
