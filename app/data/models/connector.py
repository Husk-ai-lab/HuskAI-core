from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.data.models.common import HuskModel, OrgScoped
from app.helpers.time import utcnow


class ConnectorKind(StrEnum):
    telegram = "telegram"
    gmail = "gmail"


class ConnectorState(StrEnum):
    not_configured = "not_configured"
    ready = "ready"  # verified when saved, and the last send (if any) worked
    error = "error"  # the last send or check failed: needs attention


class Connector(OrgScoped):
    """Status of one connector. Never carries a secret: `hint` is what the user recognises (the bot's @name,
    the Gmail address), `last_error` a short code (never a provider message)."""

    kind: ConnectorKind
    state: ConnectorState = ConnectorState.not_configured
    hint: str | None = None
    target_hint: str | None = None  # Telegram: the chat that receives messages (a person's first name or group title)
    verified_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def alive(self) -> bool:
        return self.state == ConnectorState.ready


class ConnectorIn(HuskModel):
    """Credentials in a request body only. Telegram: token + chat_id. Gmail: address + app_password."""

    token: str | None = Field(None, max_length=200)
    chat_id: str | None = Field(None, max_length=64)
    label: str | None = Field(None, max_length=80)  # the chat the user picked (shown, not a secret)
    address: str | None = Field(None, max_length=200)
    app_password: str | None = Field(None, max_length=64)


class ChatDetectIn(HuskModel):
    token: str = Field(..., min_length=10, max_length=200)


class ChatDetected(HuskModel):
    chat_id: str
    label: str | None = None
