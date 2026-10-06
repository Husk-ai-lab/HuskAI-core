"""The notification node: one channel (desktop, Telegram, Gmail) per node, edited by hand or by the assistant."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.automations import executor as executor_mod
from app.core.automations.executor import AutomationExecutor
from app.core.connectors.service import changes_sends, kinds_used
from app.data.models.automation import (
    ActionNode,
    Automation,
    AutomationGraph,
    Edge,
    Node,
    NotificationChannel,
    NotificationNode,
    RunTrigger,
    TriggerNode,
)
from app.data.models.connector import ConnectorKind
from app.gateways.sqlite.repositories import AutomationRepo
from test.conftest import ORG, USER, login


def _auto(channel: str = "desktop", recipients: str | None = None, aid: str = "auto_n") -> Automation:
    node = NotificationNode(id="notify", label="Avvisa", channel=channel, recipients=recipients, template="Report: {oee}")
    graph = AutomationGraph(nodes=[TriggerNode(id="trigger", label="Manuale"), node], edges=[Edge(id="e1", source="trigger", target="notify")])
    return Automation(id=aid, org_id=ORG, title="Controllo", graph=graph)


# --- model -----------------------------------------------------------------------------------------------
def test_a_notification_has_one_channel_and_defaults_to_the_desktop() -> None:
    n = NotificationNode(id="n", label="Avvisa")
    assert n.kind == "notification" and n.channel == NotificationChannel.desktop and n.recipients is None and not n.attach_report
    assert NotificationChannel.desktop.connector is None
    assert (NotificationChannel.gmail.connector, NotificationChannel.telegram.connector) == ("gmail", "telegram")
    with pytest.raises(ValidationError):
        NotificationNode(id="n", label="x", channel="sms")


def test_sending_is_no_longer_an_action() -> None:
    """Sending belongs to the notification node; the old action values do not validate (fresh start, no conversion)."""
    adapter = TypeAdapter(Node)
    for old in ("email", "telegram", "notify"):
        with pytest.raises(ValidationError):
            adapter.validate_python({"id": "a", "kind": "action", "label": "x", "action": old})
    assert adapter.validate_python({"id": "a", "kind": "action", "label": "x", "action": "pdf_report"}).action.value == "pdf_report"
    assert adapter.validate_python({"id": "n", "kind": "notification", "label": "x", "channel": "telegram"}).channel.value == "telegram"
    with pytest.raises(ValidationError):
        ActionNode(id="a", label="x", action="email")


def test_only_notifications_that_reach_others_count_as_sends() -> None:
    desktop, gmail, telegram = _auto("desktop").graph, _auto("gmail", "a@b.it").graph, _auto("telegram").graph
    assert kinds_used(desktop) == [] and kinds_used(gmail) == [ConnectorKind.gmail] and kinds_used(telegram) == [ConnectorKind.telegram]
    assert not changes_sends(desktop, _auto("desktop", "x").graph)  # a desktop notification reaches nobody else
    assert changes_sends(desktop, gmail) and changes_sends(gmail, desktop)  # switching to / from an outside channel
    assert changes_sends(gmail, _auto("gmail", "c@d.it").graph) and not changes_sends(gmail, gmail)


# --- runs ----------------------------------------------------------------------------------------------------
async def test_desktop_notification_runs_without_leaking_the_message(services, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    login(services)
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(executor_mod, "notify_desktop", lambda title, body: shown.append((title, body)) or True)
    ex = AutomationExecutor(services.agent_context(ORG, USER), tmp_path)
    a = _auto("desktop")
    prova = await ex.run(a, RunTrigger.prova)
    assert prova.status.value == "success" and prova.log[-1].status == "simulated" and shown == []  # Prova: nothing on screen
    real = await ex.run(a, RunTrigger.manual)
    assert real.status.value == "success" and real.log[-1].status == "ok" and len(shown) == 1 and shown[0][0] == "Controllo"
    assert real.log[-1].output == {"channel": "desktop", "chars": len(shown[0][1])}  # no message text in the log


async def test_the_report_made_by_an_earlier_step_can_be_attached(services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    login(services)  # (sending itself is covered in test_connectors; here only the wiring)
    pdf = ActionNode(id="pdf", label="Report", action="pdf_report", template="Esito")
    send = NotificationNode(id="send", label="Invia", channel="telegram", attach_report=True)
    g = AutomationGraph(nodes=[TriggerNode(id="trigger", label="T"), pdf, send],
                        edges=[Edge(id="e1", source="trigger", target="pdf"), Edge(id="e2", source="pdf", target="send")])
    run = await AutomationExecutor(services.agent_context(ORG, USER), tmp_path).run(Automation(id="a", org_id=ORG, title="T", graph=g), RunTrigger.prova)
    assert run.log[1].status == "ok" and run.log[1].output.endswith(".pdf")
    assert run.log[2].status == "skipped" and run.log[2].output["reason"] == "not_configured"  # Telegram is not set up here


# --- manual editing (the only thing a user sets by hand) -----------------------------------------------------------
def _put(client, **body):  # type: ignore[no-untyped-def]
    return client.put("/api/v1/automations/auto_n/nodes/notify/notification", json=body)


def test_the_user_sets_channel_and_recipients_by_hand(client, services) -> None:  # type: ignore[no-untyped-def]
    AutomationRepo(services.db, ORG).save(_auto(), change_note="x", author="test")
    r = _put(client, channel="gmail", recipients="boss@cliente.it;  hr@cliente.it boss@cliente.it", subject="Esito")
    assert r.status_code == 200, r.text
    node = next(n for n in r.json()["graph"]["nodes"] if n["id"] == "notify")
    assert (node["channel"], node["recipients"], node["subject"]) == ("gmail", "boss@cliente.it, hr@cliente.it", "Esito")
    assert r.json()["version"] == 2
    # a new version with the user as author: Undo brings the desktop notification back
    assert AutomationRepo(services.db, ORG).versions("auto_n")[0].author == USER
    undone = client.post("/api/v1/automations/auto_n/undo").json()
    assert next(n for n in undone["graph"]["nodes"] if n["id"] == "notify")["channel"] == "desktop"
    # the same edit never needs a proposal, and an unset Gmail connector becomes ONE suggestion
    _put(client, channel="gmail", recipients="boss@cliente.it")
    sug = [s for s in client.get("/api/v1/suggestions?status=all").json() if s["kind"] == "configure_connector"]
    assert len(sug) == 1 and sug[0]["connector"] == "gmail"
    # recipients can be cleared; sending the attachment switch alone leaves the rest untouched
    r = _put(client, recipients=None, attach_report=True)
    node = next(n for n in r.json()["graph"]["nodes"] if n["id"] == "notify")
    assert node["recipients"] is None and node["attach_report"] is True and node["channel"] == "gmail"


def test_manual_edits_are_validated_and_limited_to_notification_nodes(client, services) -> None:  # type: ignore[no-untyped-def]
    AutomationRepo(services.db, ORG).save(_auto(), change_note="x", author="test")
    bad = _put(client, channel="gmail", recipients="not an address, boss@cliente.it")
    assert bad.status_code == 400 and bad.json()["code"] == "errors.notification_recipients_invalid"
    too_many = _put(client, recipients=", ".join(f"p{i}@x.it" for i in range(11)))
    assert too_many.status_code == 400
    assert _put(client, channel="sms").status_code == 422
    assert client.get("/api/v1/automations/auto_n").json()["automation"]["version"] == 1  # nothing was saved
    assert client.put("/api/v1/automations/auto_n/nodes/trigger/notification", json={"channel": "gmail"}).json()["code"] == "errors.node_not_notification"
    assert client.put("/api/v1/automations/auto_n/nodes/nope/notification", json={"channel": "gmail"}).status_code == 404
    assert client.put("/api/v1/automations/zzz/nodes/notify/notification", json={"channel": "gmail"}).status_code == 404
    # only the five delivery fields can be sent this way: a label or position change is ignored
    ok = _put(client, label="Hacked", position={"x": 9, "y": 9}, template="Nuovo testo")
    node = next(n for n in ok.json()["graph"]["nodes"] if n["id"] == "notify")
    assert node["label"] == "Avvisa" and node["template"] == "Nuovo testo"


def test_the_builder_is_told_about_notification_channels(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    block = services.agent_context(ORG, USER).connectors_block()
    assert "desktop: always available" in block and "telegram: NOT set up" in block and "gmail: NOT set up" in block
    from app.core.agents.builder import prompts

    assert "kind 'notification'" in prompts.CONNECTORS and "Never use an 'action' node to send" in prompts.CONNECTORS


# --- old rows ---------------------------------------------------------------------------------------------------------
def test_saved_automations_from_an_older_version_are_skipped_not_fatal(client, services) -> None:  # type: ignore[no-untyped-def]
    repo = AutomationRepo(services.db, ORG)
    repo.save(_auto(aid="auto_ok"), change_note="x", author="test")
    old = repo.get("auto_ok").model_dump_json().replace('"kind":"notification","channel":"desktop"', '"kind":"action","action":"email"')
    assert '"action":"email"' in old
    with services.db.tx() as c:
        c.execute("INSERT INTO automations(id, org_id, status, data, updated_at) VALUES ('auto_old', ?, 'attiva', ?, '2026-10-01T00:00:00+00:00')", (ORG, old))
    assert [a.id for a in repo.list()] == ["auto_ok"]
    assert [a.id for a in repo.all_active()] == []  # the scheduler starts fine without it
    assert client.get("/api/v1/automations").status_code == 200
    assert client.get("/api/v1/automations/auto_old").status_code == 404
