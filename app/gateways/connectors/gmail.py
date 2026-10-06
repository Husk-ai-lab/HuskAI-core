"""Gmail over SMTP with an App Password (smtp.gmail.com:465, SSL). The customer's own Google account: mail goes
from this computer straight to Google, not through Husk's servers.

An App Password needs 2-Step Verification and is not available on most work/school (Workspace) accounts, nor with
Advanced Protection. Google revokes it when the account password changes: the next send fails with `auth_failed`
and the connector turns "needs attention". Exception text is never kept (it can hold addresses)."""

from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from app.gateways.connectors.errors import ConnectorError

HOST, PORT, TIMEOUT_S = "smtp.gmail.com", 465, 20
MAX_RECIPIENTS = 10
MAX_ATTACHMENT = 20 * 1024 * 1024
_ADDRESS = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def is_address(value: str) -> bool:
    return bool(_ADDRESS.match(value.strip()))


def parse_recipients(target: str | None) -> list[str]:
    """Valid addresses out of 'a@x.it, b@y.it; c@z.it' (anything else, like a role name, is dropped)."""
    found = [p.strip() for p in re.split(r"[,;\s]+", target or "") if is_address(p)]
    return list(dict.fromkeys(found))[:MAX_RECIPIENTS]


def clean_password(value: str) -> str:
    return re.sub(r"\s+", "", value or "")  # Google shows it in groups of four letters


def build_message(sender: str, to: list[str], subject: str, body: str, attachment: Path | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject.replace("\n", " ")[:200] or "-"
    msg.set_content(body or "-")
    if attachment is not None:
        if attachment.stat().st_size > MAX_ATTACHMENT:
            raise ConnectorError("attachment_too_large")
        msg.add_attachment(attachment.read_bytes(), maintype="application", subtype="octet-stream", filename=attachment.name)
    return msg


def _smtp(address: str, password: str, message: EmailMessage | None) -> None:
    try:
        with smtplib.SMTP_SSL(HOST, PORT, timeout=TIMEOUT_S, context=ssl.create_default_context()) as smtp:
            smtp.login(address, password)
            if message is not None:
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise ConnectorError("auth_failed") from None
    except smtplib.SMTPRecipientsRefused:
        raise ConnectorError("recipient_refused") from None
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
        raise ConnectorError("unreachable") from None
    except smtplib.SMTPException:  # (an OSError subclass: keep it before the plain OSError below)
        raise ConnectorError("failed") from None
    except OSError:  # DNS, refused connection, timeout, TLS
        raise ConnectorError("unreachable") from None


async def verify(address: str, password: str) -> None:
    """The login is accepted (nothing is sent)."""
    await asyncio.to_thread(_smtp, address, password, None)


async def send(address: str, password: str, to: list[str], subject: str, body: str, attachment: Path | None = None) -> int:
    message = build_message(address, to, subject, body, attachment)
    await asyncio.to_thread(_smtp, address, password, message)
    return len(to)
