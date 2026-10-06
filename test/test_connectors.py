"""Connectors (Telegram, Gmail): verify before save, secrets only in the keychain, sends from automation runs,
Prova never reaches third parties, the Builder knows what is alive, and a dead connector becomes a suggestion."""

from __future__ import annotations

import logging
import smtplib
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core.automations.executor import AutomationExecutor
from app.core.connectors.service import ConnectorProblem, changes_sends, kinds_used
from app.data.models.automation import ActionNode, Automation, AutomationGraph, Edge, RunTrigger, TriggerNode
from app.data.models.connector import ConnectorIn, ConnectorKind, ConnectorState
from app.gateways.connectors import gmail, telegram
from app.gateways.connectors.errors import ConnectorError
from test.conftest import ORG, USER, login

TOKEN = "123456789:AAH-secret_bot_token_VALUE"
BOT = r"https://api.telegram.org/bot[^/]+/"
ADDRESS = "reports@officine-esempio.it"
APP_PASSWORD = "abcdefghijklmnop"


# --- fakes ----------------------------------------------------------------------------------------------
class FakeSMTP:
    sent: list = []
    login_error: Exception | None = None
    logins: list = []

    def __init__(self, host, port, timeout=None, context=None):  # type: ignore[no-untyped-def]
        self.host, self.port = host, port

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *exc):  # type: ignore[no-untyped-def]
        return False

    def login(self, user, password):  # type: ignore[no-untyped-def]
        FakeSMTP.logins.append((user, password))
        if FakeSMTP.login_error:
            raise FakeSMTP.login_error

    def send_message(self, msg):  # type: ignore[no-untyped-def]
        FakeSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent, FakeSMTP.login_error, FakeSMTP.logins = [], None, []
    monkeypatch.setattr(gmail.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def _telegram_ok(chat_id: int = 4242, label: str = "Mario") -> dict[str, respx.Route]:
    return {
        "getMe": respx.post(url__regex=BOT + "getMe").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"username": "husk_test_bot"}})),
        "getUpdates": respx.post(url__regex=BOT + "getUpdates").mock(return_value=httpx.Response(200, json={"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 1, "type": "private", "first_name": "Old"}}},
            {"update_id": 2, "message": {"chat": {"id": chat_id, "type": "private", "first_name": label}}}]})),
        "sendMessage": respx.post(url__regex=BOT + "sendMessage").mock(return_value=httpx.Response(200, json={"ok": True, "result": {}})),
        "sendDocument": respx.post(url__regex=BOT + "sendDocument").mock(return_value=httpx.Response(200, json={"ok": True, "result": {}})),
    }


async def _setup_both(services) -> None:  # type: ignore[no-untyped-def]
    await services.connectors.configure(ORG, ConnectorKind.telegram, ConnectorIn(token=TOKEN, chat_id="4242", label="Mario"))
    await services.connectors.configure(ORG, ConnectorKind.gmail, ConnectorIn(address=ADDRESS, app_password="abcd efgh ijkl mnop"))


def _graph(action: str, **kw) -> AutomationGraph:  # type: ignore[no-untyped-def]
    node = ActionNode(id="send", label="Invia", action=action, template="Report: {oee}", subject="Esito {oee}", **kw)
    return AutomationGraph(nodes=[TriggerNode(id="trigger", label="Manuale"), node], edges=[Edge(id="e1", source="trigger", target="send")])


# --- Telegram client ----------------------------------------------------------------------------------------
def test_long_messages_are_split_at_line_breaks() -> None:
    text = ("riga " * 30 + "\n") * 80  # ~12 000 characters
    parts = telegram.split_text(text)
    assert len(parts) >= 3 and all(1 <= len(p) <= telegram.MAX_TEXT for p in parts)
    assert telegram.split_text("") == ["-"]


@respx.mock
async def test_telegram_detects_the_latest_chat_and_explains_a_webhook() -> None:
    _telegram_ok()
    async with httpx.AsyncClient() as http:
        assert await telegram.verify(http, TOKEN) == "@husk_test_bot"
        assert await telegram.detect_chat(http, TOKEN) == {"chat_id": "4242", "label": "Mario"}
    respx.post(url__regex=BOT + "getUpdates").mock(return_value=httpx.Response(200, json={"ok": True, "result": []}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError) as e:
            await telegram.detect_chat(http, TOKEN)
    assert e.value.code == "no_chat"
    respx.post(url__regex=BOT + "getUpdates").mock(return_value=httpx.Response(409, json={"ok": False, "description": "Conflict: can't use getUpdates method while webhook is active"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError) as e:
            await telegram.detect_chat(http, TOKEN)
    assert e.value.code == "webhook_active"


@respx.mock
async def test_telegram_errors_are_codes_and_never_carry_the_token(tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
    from app.helpers.logging import configure_logging

    configure_logging(tmp_path / "logs")  # what the app does at start: httpx (which logs URLs) is kept at WARNING
    caplog.set_level(logging.DEBUG)
    respx.post(url__regex=BOT + "getMe").mock(return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError) as e:
            await telegram.verify(http, TOKEN)
    assert e.value.code == "invalid_token"
    respx.post(url__regex=BOT + "getMe").mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConnectorError) as e:
            await telegram.verify(http, TOKEN)
    assert e.value.code == "unreachable" and TOKEN not in str(e.value) and e.value.__cause__ is None
    assert TOKEN not in "".join(r.getMessage() for r in caplog.records if r.name != "httpx2")


@respx.mock
async def test_telegram_paces_splits_retries_once_and_sends_the_report(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(telegram, "asyncio", SimpleNamespace(sleep=fake_sleep))
    routes = _telegram_ok()
    routes["sendMessage"].mock(side_effect=[
        httpx.Response(429, json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 3}}),
        httpx.Response(200, json={"ok": True, "result": {}}),
        httpx.Response(200, json={"ok": True, "result": {}}),
    ])
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    async with httpx.AsyncClient() as http:
        n = await telegram.send(http, TOKEN, "4242", ("x" * 4000 + "\n") * 2, pdf)
    assert n == 3 and routes["sendMessage"].call_count == 3 and routes["sendDocument"].call_count == 1
    assert 3.5 in slept and telegram.PACE_S in slept  # honoured retry_after, one message per second


# --- Gmail client ----------------------------------------------------------------------------------------------
def test_recipients_are_addresses_only() -> None:
    assert gmail.parse_recipients("a@x.it, b@y.it; a@x.it  c@z.com") == ["a@x.it", "b@y.it", "c@z.com"]
    assert gmail.parse_recipients("il responsabile acquisti") == []
    assert gmail.parse_recipients(None) == []
    assert gmail.clean_password("abcd efgh\tijkl mnop") == APP_PASSWORD


async def test_gmail_login_errors_are_codes(fake_smtp) -> None:  # type: ignore[no-untyped-def]
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    with pytest.raises(ConnectorError) as e:
        await gmail.verify(ADDRESS, APP_PASSWORD)
    assert e.value.code == "auth_failed" and ADDRESS not in str(e.value)
    fake_smtp.login_error = smtplib.SMTPDataError(554, b"rejected")
    with pytest.raises(ConnectorError) as e:
        await gmail.verify(ADDRESS, APP_PASSWORD)
    assert e.value.code == "failed"
    fake_smtp.login_error = OSError("dns")
    with pytest.raises(ConnectorError) as e:
        await gmail.verify(ADDRESS, APP_PASSWORD)
    assert e.value.code == "unreachable"


# --- service: verify before save, secrets only in the keychain -----------------------------------------------------
@respx.mock
async def test_nothing_is_saved_unless_the_provider_accepts(services, fake_smtp) -> None:  # type: ignore[no-untyped-def]
    respx.post(url__regex=BOT + "getMe").mock(return_value=httpx.Response(401, json={"ok": False}))
    with pytest.raises(ConnectorProblem) as e:
        await services.connectors.configure(ORG, ConnectorKind.telegram, ConnectorIn(token=TOKEN, chat_id="1"))
    assert e.value.key == "errors.connector_invalid_token"
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"no")
    with pytest.raises(ConnectorProblem) as e:
        await services.connectors.configure(ORG, ConnectorKind.gmail, ConnectorIn(address=ADDRESS, app_password=APP_PASSWORD))
    assert e.value.key == "errors.connector_auth_failed"
    with pytest.raises(ConnectorProblem) as e:
        await services.connectors.configure(ORG, ConnectorKind.gmail, ConnectorIn(address="not-an-address", app_password="x"))
    assert e.value.key == "errors.connector_invalid_input"
    with pytest.raises(ConnectorProblem) as e:
        await services.connectors.configure(ORG, ConnectorKind.telegram, ConnectorIn(token=TOKEN))  # no chat chosen yet
    assert e.value.key == "errors.connector_no_chat"
    assert [c.state for c in services.connectors.status(ORG)] == [ConnectorState.not_configured] * 2
    assert services.connectors.secrets.get(ORG, "telegram") is None and services.connectors.secrets.get(ORG, "gmail") is None


@respx.mock
async def test_secrets_live_in_the_keychain_only(services, caplog, tmp_path) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    _telegram_ok()
    await _setup_both(services)
    st = {c.kind.value: c for c in services.connectors.status(ORG)}
    assert st["telegram"].alive and st["telegram"].hint == "@husk_test_bot" and st["telegram"].target_hint == "Mario"
    assert st["gmail"].alive and st["gmail"].hint == ADDRESS
    assert FakeSMTP.logins[-1] == (ADDRESS, APP_PASSWORD)  # spaces stripped
    with services.db.read() as conn:
        dump = "\n".join(conn.iterdump())
    for secret in (TOKEN, APP_PASSWORD, "4242"):
        assert secret not in dump  # status only in SQLite
    assert TOKEN not in "".join(r.getMessage() for r in caplog.records if r.name != "httpx2")
    # a backup never carries them either
    from app.gateways.sqlite.backup import export_backup

    out = export_backup(services.db, services.paths, "test")
    raw = out.read_bytes()
    assert TOKEN.encode() not in raw and APP_PASSWORD.encode() not in raw
    import sqlite3

    assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM connectors").fetchone()[0] == 0
    # removing deletes the credential
    services.connectors.remove(ORG, ConnectorKind.gmail)
    assert services.connectors.get(ORG, ConnectorKind.gmail).state == ConnectorState.not_configured
    assert services.connectors.secrets.get(ORG, "gmail") is None


async def test_status_survives_an_imported_backup_but_not_a_missing_credential(services) -> None:  # type: ignore[no-untyped-def]
    services.connectors.secrets.set(ORG, "gmail", {"address": ADDRESS, "password": APP_PASSWORD, "hint": ADDRESS})
    assert services.connectors.get(ORG, ConnectorKind.gmail).alive  # credential present, status row lost (import)
    services.connectors.secrets.delete(ORG, "gmail")
    assert not services.connectors.get(ORG, ConnectorKind.gmail).alive  # status row without a credential: not alive


# --- API ---------------------------------------------------------------------------------------------------------
@respx.mock
def test_api_never_returns_a_secret(client, services) -> None:  # type: ignore[no-untyped-def]
    _telegram_ok()
    assert [c["state"] for c in client.get("/api/v1/connectors").json()] == ["not_configured", "not_configured"]
    found = client.post("/api/v1/connectors/telegram/detect-chat", json={"token": TOKEN}).json()
    assert found == {"chat_id": "4242", "label": "Mario"}
    r = client.put("/api/v1/connectors/telegram", json={"token": TOKEN, "chat_id": found["chat_id"], "label": found["label"]})
    assert r.status_code == 200 and r.json()["hint"] == "@husk_test_bot" and TOKEN not in r.text and "4242" not in r.text
    assert client.post("/api/v1/connectors/telegram/test").json()["state"] == "ready"
    r = client.put("/api/v1/connectors/gmail", json={"address": ADDRESS, "app_password": APP_PASSWORD})
    assert r.status_code == 200 and APP_PASSWORD not in r.text
    assert FakeSMTP.sent == []  # saving only logs in; nothing is sent until the user asks for the test message
    assert client.post("/api/v1/connectors/gmail/test").json()["state"] == "ready"
    assert len(FakeSMTP.sent) == 1 and FakeSMTP.sent[0]["To"] == ADDRESS
    listed = client.get("/api/v1/connectors").text
    assert TOKEN not in listed and APP_PASSWORD not in listed
    assert client.delete("/api/v1/connectors/gmail").json()["state"] == "not_configured"


@respx.mock
def test_api_reports_why_a_connector_is_refused(client, fake_smtp) -> None:  # type: ignore[no-untyped-def]
    respx.post(url__regex=BOT + "getMe").mock(return_value=httpx.Response(401, json={"ok": False}))
    r = client.put("/api/v1/connectors/telegram", json={"token": TOKEN, "chat_id": "1"})
    assert r.status_code == 400 and r.json()["code"] == "errors.connector_invalid_token"
    fake_smtp.login_error = OSError("offline")
    r = client.put("/api/v1/connectors/gmail", json={"address": ADDRESS, "app_password": APP_PASSWORD})
    assert r.status_code == 503 and r.json()["code"] == "errors.connector_unreachable"
    assert client.post("/api/v1/connectors/gmail/test").json()["code"] == "errors.connector_not_configured"


# --- automation runs ---------------------------------------------------------------------------------------------
@respx.mock
async def test_runs_send_log_no_content_and_prova_stays_with_the_user(services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    login(services)
    routes = _telegram_ok()
    await _setup_both(services)
    ex = AutomationExecutor(services.agent_context(ORG, USER), tmp_path)

    mail = Automation(id="a1", org_id=ORG, title="Report", graph=_graph("email", target="boss@cliente.it, hr@cliente.it"))
    run = await ex.run(mail, RunTrigger.manual)
    assert run.status.value == "success", run.error
    entry = run.log[-1]
    assert entry.status == "ok" and entry.output == {"connector": "gmail", "chars": len("Report: {oee}"), "recipients": 2}
    assert FakeSMTP.sent[-1]["To"] == "boss@cliente.it, hr@cliente.it" and FakeSMTP.sent[-1]["From"] == ADDRESS
    assert "boss@cliente.it" not in str(entry.model_dump())  # no address in the persisted log

    prova = await ex.run(mail, RunTrigger.prova)  # Prova: only to the connector's own address
    assert FakeSMTP.sent[-1]["To"] == ADDRESS and FakeSMTP.sent[-1]["Subject"].startswith("[Test]")  # English UI: "[Prova]" in Italian
    assert prova.log[-1].status == "ok"

    tg = Automation(id="a2", org_id=ORG, title="Avviso", graph=_graph("telegram"))
    run = await ex.run(tg, RunTrigger.manual)
    assert run.status.value == "success" and run.log[-1].output["connector"] == "telegram"
    assert routes["sendMessage"].call_count == 1
    assert "4242" not in str(run.model_dump()) and TOKEN not in str(run.model_dump())


async def test_a_connector_that_is_not_set_up_skips_the_step_and_the_run_goes_on(services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    login(services)
    ex = AutomationExecutor(services.agent_context(ORG, USER), tmp_path)
    for action in ("email", "telegram"):
        a = Automation(id=f"a_{action}", org_id=ORG, title="T", graph=_graph(action, target="x@y.it"))
        run = await ex.run(a, RunTrigger.manual)
        assert run.status.value == "success" and run.log[-1].status == "skipped"
        assert run.log[-1].output["reason"] == "not_configured"
    await services.connectors.configure(ORG, ConnectorKind.gmail, ConnectorIn(address=ADDRESS, app_password=APP_PASSWORD))
    a = Automation(id="a_role", org_id=ORG, title="T", graph=_graph("email", target="il responsabile acquisti"))
    run = await ex.run(a, RunTrigger.manual)
    assert run.log[-1].status == "skipped" and run.log[-1].output["reason"] == "no_recipient"  # a role is not an address


async def test_a_failed_send_fails_the_run_without_leaking_and_marks_the_connector(services, tmp_path, fake_smtp) -> None:  # type: ignore[no-untyped-def]
    login(services)
    await services.connectors.configure(ORG, ConnectorKind.gmail, ConnectorIn(address=ADDRESS, app_password=APP_PASSWORD))
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"revoked " + ADDRESS.encode())  # password changed at Google
    ex = AutomationExecutor(services.agent_context(ORG, USER), tmp_path)
    a = Automation(id="a3", org_id=ORG, title="T", graph=_graph("email", target="boss@cliente.it"))
    run = await ex.run(a, RunTrigger.manual)
    assert run.status.value == "failed" and "Gmail" in (run.error or "")
    assert ADDRESS not in str(run.model_dump()) and "boss@cliente.it" not in str(run.model_dump())
    cn = services.connectors.get(ORG, ConnectorKind.gmail)
    assert cn.state == ConnectorState.error and cn.last_error == "auth_failed"


# --- the Builder knows what is alive ------------------------------------------------------------------------------------
@respx.mock
async def test_builder_is_told_which_connectors_work(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    ctx = services.agent_context(ORG, USER)
    block = ctx.connectors_block()
    assert "telegram: NOT set up" in block and "gmail: NOT set up" in block
    _telegram_ok()
    await services.connectors.configure(ORG, ConnectorKind.telegram, ConnectorIn(token=TOKEN, chat_id="4242"))
    assert "telegram: ready" in ctx.connectors_block() and "gmail: NOT set up" in ctx.connectors_block()
    services.connectors._mark(ORG, ConnectorKind.telegram, "chat_not_found")
    assert "telegram: needs attention" in ctx.connectors_block()


def test_a_change_to_a_sending_step_is_detected() -> None:
    before = _graph("email", target="a@b.it")
    assert not changes_sends(before, before)
    assert changes_sends(before, _graph("email", target="c@d.it"))
    assert changes_sends(_graph("log"), before)  # a step that sends was added
    assert kinds_used(before) == [ConnectorKind.gmail] and kinds_used(_graph("log")) == []


# --- suggestions --------------------------------------------------------------------------------------------------------
@respx.mock
async def test_a_dead_connector_becomes_one_suggestion_and_closes_when_fixed(client, services) -> None:  # type: ignore[no-untyped-def]
    from app.data.models.automation import AutomationStatus
    from app.gateways.sqlite.repositories import AutomationRepo, SuggestionRepo

    auto = Automation(id="auto_mail", org_id=ORG, title="Report settimanale", graph=_graph("email", target="boss@cliente.it"))
    AutomationRepo(services.db, ORG).save(auto, change_note="x", author="test")
    for _ in range(2):  # activating twice: still ONE suggestion for the connector
        client.post(f"/api/v1/automations/{auto.id}/status", json={"status": AutomationStatus.attiva.value})
    sugs = [s for s in client.get("/api/v1/suggestions?status=all").json() if s["kind"] == "configure_connector"]
    assert len(sugs) == 1 and sugs[0]["connector"] == "gmail" and sugs[0]["automation_id"] == auto.id
    assert "Gmail" in sugs[0]["title"] and "Report settimanale" in sugs[0]["body"]
    client.put("/api/v1/connectors/gmail", json={"address": ADDRESS, "app_password": APP_PASSWORD})  # the user sets it up
    assert SuggestionRepo(services.db, ORG).find_open_connector("gmail") is None  # closed by itself
    # later the Google password changes: the next run fails and the connector is reported again
    FakeSMTP.login_error = smtplib.SMTPAuthenticationError(535, b"revoked")
    client.post(f"/api/v1/automations/{auto.id}/test")  # a test run: Prova sends to the user's own address
    again = [s for s in client.get("/api/v1/suggestions?status=new").json() if s["kind"] == "configure_connector"]
    assert len(again) == 1 and "stopped working" in again[0]["title"]
