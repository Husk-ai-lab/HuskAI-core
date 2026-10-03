from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.data.models.common import HuskModel, OrgScoped
from app.data.models.user import PersonalityId
from app.helpers.time import utcnow


class Personality(HuskModel):
    id: PersonalityId
    label: str
    description: str
    prompt: str
    requires_confirmation: bool = False  # builder proposes changes instead of applying them


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    tool = "tool"
    system = "system"


class Chat(OrgScoped):
    id: str
    user_id: str
    automation_id: str | None = None
    title: str = "Nuova conversazione"
    personality: PersonalityId = PersonalityId.bilanciato
    created_at: datetime = Field(default_factory=utcnow)


class ChatMessage(HuskModel):
    id: str
    chat_id: str
    role: MessageRole
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)  # tool calls, proposals, studio link
    created_at: datetime = Field(default_factory=utcnow)


class ChatRequest(HuskModel):
    content: str = Field(..., min_length=1, max_length=4000)
    selected_node_id: str | None = None
    personality: PersonalityId | None = None
    suggestion_id: str | None = None  # sent from a Husk suggestion (pre-made prompt), see Builder rules


class ProposalStatus(StrEnum):
    pending = "pending"
    applied = "applied"
    rejected = "rejected"


class Proposal(OrgScoped):
    """A graph change waiting for user confirmation (ultra-careful personality)."""

    id: str
    chat_id: str
    automation_id: str
    summary: str
    operations: list[dict[str, Any]]
    status: ProposalStatus = ProposalStatus.pending
    suggestion_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentEvent(HuskModel):
    """Streamed to the UI over SSE while the builder works."""

    type: str  # status | tool | patch | proposal | message | error | done
    data: dict[str, Any] = Field(default_factory=dict)


class AgentCheckpoint(OrgScoped):
    id: str
    run_id: str
    agent: str
    step: str
    state: dict[str, Any]
    created_at: datetime = Field(default_factory=utcnow)


class TokenUsage(OrgScoped):
    user_id: str
    day: str  # UTC date
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
