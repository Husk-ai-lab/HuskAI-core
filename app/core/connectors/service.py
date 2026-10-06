"""Connectors (Telegram, Gmail): configure, verify, send, and tell the Builder which ones are alive.

* A connector is saved only after the provider accepted it (Telegram `getMe`, Gmail SMTP login).
* Credentials live in the OS keychain (`ConnectorSecrets`); SQLite keeps the status only.
* A send never logs or stores a message body, an address, a chat id or a token: node logs get
  `{connector, chars, recipients}` and failures become short codes (see `ConnectorError`).
* In a test run ("Prova") nothing reaches a third party: Telegram goes to the configured chat (the user's own) and
  email goes ONLY to the connector's own address."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.data.models.automation import AutomationGraph, NotificationNode
from app.data.models.connector import Connector, ConnectorIn, ConnectorKind, ConnectorState
from app.gateways.connectors import gmail, telegram
from app.gateways.connectors.errors import ConnectorError
from app.gateways.connectors.secrets import ConnectorSecrets
from app.gateways.sqlite import Database
from app.gateways.sqlite.repositories import ConnectorRepo
from app.helpers.logging import get_logger
from app.helpers.time import utcnow
from app.i18n import t
from app.i18n.errors import LocalizedError

log = get_logger("connectors")

KINDS = (ConnectorKind.telegram, ConnectorKind.gmail)
_HTTP_STATUS = {"unreachable": 503, "rate_limited": 429}


class ConnectorProblem(LocalizedError):
    """A connector call failed, as an API error (`errors.connector_<code>`)."""

    def __init__(self, code: str):
        super().__init__(f"errors.connector_{code}", status=_HTTP_STATUS.get(code, 400))
        self.code = code


@dataclass
class SendResult:
    status: str  # ok | skipped | error
    output: dict[str, Any] = field(default_factory=dict)
    message_key: str = ""
    params: dict[str, Any] = field(default_factory=dict)


def kinds_used(graph: AutomationGraph) -> list[ConnectorKind]:
    """The connectors an automation sends through (its notification nodes), in a stable order."""
    used = {ConnectorKind(n.channel.connector) for n in graph.nodes if isinstance(n, NotificationNode) and n.channel.connector}
    return [k for k in KINDS if k in used]


def changes_sends(before: AutomationGraph, after: AutomationGraph) -> bool:
    """True when a notification that reaches people outside this computer (Telegram, Gmail) was added, changed,
    switched to or from such a channel, or removed. Code, not a prompt: such changes always go to the user as a
    proposal. A desktop notification reaches nobody else, so it does not count."""
    def sig(g: AutomationGraph) -> dict[str, tuple]:
        return {n.id: (n.channel.value, n.recipients, n.template, n.subject, n.attach_report)
                for n in g.nodes if isinstance(n, NotificationNode) and n.channel.connector}
    return sig(before) != sig(after)


class ConnectorService:
    def __init__(self, db: Database, secrets: ConnectorSecrets):
        self.db, self.secrets = db, secrets

    def _repo(self, org_id: str) -> ConnectorRepo:
        return ConnectorRepo(self.db, org_id)

    # --- status -------------------------------------------------------------------------------
    def get(self, org_id: str, kind: ConnectorKind) -> Connector:
        secret = self.secrets.get(org_id, kind.value)
        row = self._repo(org_id).get(kind)
        if secret is None:  # never set up, removed, or the keychain is empty (new machine, restart without keychain)
            return Connector(org_id=org_id, kind=kind)
        if row is None:  # e.g. after importing a backup: the credential is here, the status row is not
            return Connector(org_id=org_id, kind=kind, state=ConnectorState.ready, hint=secret.get("hint"),
                             target_hint=secret.get("label"))
        return row

    def status(self, org_id: str) -> list[Connector]:
        return [self.get(org_id, k) for k in KINDS]

    def prompt_block(self, org_id: str) -> str:
        """For the Builder: the channels a notification node can use, and whether each works right now."""
        lines = ["- desktop: always available — a notification on this computer (nobody else is reached)"]
        how = {
            ConnectorKind.telegram: "a message (and optionally the PDF report) to the configured Telegram chat",
            ConnectorKind.gmail: "an email to the node's recipients (address(es)), with subject and body, optionally with the PDF report, from the configured Gmail account",
        }
        for c in self.status(org_id):
            state = {"ready": "ready", "error": "needs attention (the last send failed)", "not_configured": "NOT set up"}[c.state.value]
            lines.append(f"- {c.kind.value}: {state} — {how[c.kind]}")
        return "\n".join(lines)

    # --- configure ----------------------------------------------------------------------------
    async def detect_chat(self, token: str) -> dict[str, str | None]:
        try:
            async with httpx.AsyncClient() as http:
                await telegram.verify(http, token.strip())
                return await telegram.detect_chat(http, token.strip())
        except ConnectorError as exc:
            raise ConnectorProblem(exc.code) from None

    async def configure(self, org_id: str, kind: ConnectorKind, data: ConnectorIn) -> Connector:
        """Verify with the provider first; nothing is saved if it is refused."""
        if kind == ConnectorKind.telegram:
            token, chat = (data.token or "").strip(), (data.chat_id or "").strip()
            if not token:
                raise ConnectorProblem("invalid_input")
            if not chat:
                raise ConnectorProblem("no_chat")
            try:
                async with httpx.AsyncClient() as http:
                    hint = await telegram.verify(http, token)
            except ConnectorError as exc:
                raise ConnectorProblem(exc.code) from None
            label = (data.label or "").strip() or None
            blob = {"token": token, "chat_id": chat, "hint": hint, "label": label or ""}
        else:
            address, password = (data.address or "").strip().lower(), gmail.clean_password(data.app_password or "")
            if not gmail.is_address(address) or len(password) < 8:
                raise ConnectorProblem("invalid_input")
            try:
                await gmail.verify(address, password)
            except ConnectorError as exc:
                raise ConnectorProblem(exc.code) from None
            hint, label = address, None
            blob = {"address": address, "password": password, "hint": address}
        self.secrets.set(org_id, kind.value, blob)
        now = utcnow()
        return self._repo(org_id).save(Connector(org_id=org_id, kind=kind, state=ConnectorState.ready, hint=hint,
                                                 target_hint=label, verified_at=now))

    def remove(self, org_id: str, kind: ConnectorKind) -> None:
        self.secrets.delete(org_id, kind.value)
        self._repo(org_id).delete(kind)

    async def test(self, org_id: str, kind: ConnectorKind, lang: str) -> Connector:
        """Sends a short test message to the user themselves; the connector turns ready or "needs attention"."""
        secret = self.secrets.get(org_id, kind.value)
        if secret is None:
            raise ConnectorProblem("not_configured")
        try:
            if kind == ConnectorKind.telegram:
                async with httpx.AsyncClient() as http:
                    await telegram.send(http, secret["token"], secret["chat_id"], t("connectors.test_telegram", lang))
            else:
                await gmail.send(secret["address"], secret["password"], [secret["address"]],
                                 t("connectors.test_gmail_subject", lang), t("connectors.test_gmail_body", lang))
        except ConnectorError as exc:
            self._mark(org_id, kind, exc.code)
            raise ConnectorProblem(exc.code) from None
        return self._mark(org_id, kind, None)

    def _mark(self, org_id: str, kind: ConnectorKind, error: str | None) -> Connector:
        cn = self.get(org_id, kind)
        if cn.state == ConnectorState.not_configured:
            return cn
        if error:
            cn.state, cn.last_error = ConnectorState.error, error
        else:
            cn.state, cn.last_error, cn.last_ok_at = ConnectorState.ready, None, utcnow()
        return self._repo(org_id).save(cn)

    # --- sending (automation runs) --------------------------------------------------------------
    async def send(self, org_id: str, kind: ConnectorKind, *, subject: str, body: str, target: str | None,
                   attachment: Path | None, test_mode: bool, lang: str) -> SendResult:
        name = t(f"connectors.name_{kind.value}", lang)
        secret = self.secrets.get(org_id, kind.value)
        if secret is None:
            return SendResult("skipped", {"connector": kind.value, "reason": "not_configured"}, "run.connector_not_ready",
                              {"connector": name})
        prefix = t("run.prova_prefix", lang) + " " if test_mode else ""
        try:
            if kind == ConnectorKind.telegram:
                async with httpx.AsyncClient() as http:
                    await telegram.send(http, secret["token"], secret["chat_id"], prefix + (body or subject), attachment)
                n, key = 1, "run.connector_sent_telegram"
            else:
                to = [secret["address"]] if test_mode else gmail.parse_recipients(target)
                if not to:
                    return SendResult("skipped", {"connector": kind.value, "reason": "no_recipient"}, "run.email_no_recipient", {})
                n = await gmail.send(secret["address"], secret["password"], to, prefix + subject, body, attachment)
                key = "run.connector_sent_gmail_test" if test_mode else "run.connector_sent_gmail"
        except ConnectorError as exc:
            self._mark(org_id, kind, exc.code)
            log.warning("connector send failed kind=%s code=%s", kind.value, exc.code)
            return SendResult("error", {"connector": kind.value, "code": exc.code}, "run.connector_failed",
                              {"connector": name, "reason": t(f"connectors.error_{exc.code}", lang)})
        self._mark(org_id, kind, None)
        return SendResult("ok", {"connector": kind.value, "chars": len(body), "recipients": n}, key, {"count": n})
