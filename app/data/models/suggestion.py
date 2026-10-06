"""Suggestions: what Husk noticed after documents were added or removed.

A suggestion is a hypothesis with evidence. It never changes an automation by itself: its prompt
is sent to the Builder as a *suggestion message*, which always ends in a proposal the user applies.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.data.models.common import HuskModel, OrgScoped
from app.helpers.time import utcnow


class SuggestionKind(StrEnum):
    modify_automation = "modify_automation"
    new_automation = "new_automation"
    memory_update = "memory_update"
    source_deleted = "source_deleted"
    run_discovery = "run_discovery"
    configure_connector = "configure_connector"  # a connector the automations use is not set up or stopped working


class SuggestionStatus(StrEnum):
    new = "new"
    seen = "seen"
    applied = "applied"
    dismissed = "dismissed"


OPEN_STATUSES = (SuggestionStatus.new, SuggestionStatus.seen)


class SuggestionEvidence(HuskModel):
    document_id: str
    chunk_id: str | None = None
    quote: str = Field("", max_length=400)


class Suggestion(OrgScoped):
    id: str
    kind: SuggestionKind
    status: SuggestionStatus = SuggestionStatus.new
    automation_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    title: str = Field(..., max_length=160)
    body: str = Field("", max_length=1500)
    prompt: str | None = Field(None, max_length=2000)  # pre-made Builder request (modify/source_deleted)
    evidence: list[SuggestionEvidence] = Field(default_factory=list)
    confidence: float = Field(1.0, ge=0, le=1)
    hours_per_week: float | None = Field(None, ge=0, le=80)  # new_automation estimate
    connector: str | None = None  # configure_connector: which one (telegram | gmail)
    created_by: str | None = None  # user whose upload/delete started it
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SuggestionSummary(HuskModel):
    unread: int
    open: int
    processing: bool
