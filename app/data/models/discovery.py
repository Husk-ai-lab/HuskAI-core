from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.data.models.common import HuskModel, OrgScoped
from app.helpers.time import utcnow


class DiscoveryStepId(StrEnum):
    """The 7 visible steps of a run (technical plan → Discovery agents)."""

    load = "load"
    knowledge_base = "knowledge_base"
    extract = "extract"
    repetitive = "repetitive"
    handoffs = "handoffs"
    impact = "impact"
    map = "map"


STEP_ORDER: list[DiscoveryStepId] = list(DiscoveryStepId)


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting_network = "waiting_network"  # "in attesa di connessione"
    paused = "paused"
    failed = "failed"
    completed = "completed"


class StepStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class DiscoveryRun(OrgScoped):
    id: str
    status: RunStatus = RunStatus.pending
    current_step: DiscoveryStepId | None = None
    progress: float = 0.0
    started_by: str
    document_ids: list[str] = Field(default_factory=list)
    error: str | None = None  # English text (logs); the UI renders error_key/error_params
    error_key: str | None = None
    error_params: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None  # OpenTelemetry trace id, for the Husk Studio link
    traced: bool = False  # the trace was exported to a running Husk Studio
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class DiscoveryStep(HuskModel):
    run_id: str
    step: DiscoveryStepId
    status: StepStatus = StepStatus.pending
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Priority(StrEnum):
    alta = "alta"
    media = "media"
    bassa = "bassa"


class Opportunity(OrgScoped):
    id: str
    run_id: str
    network_id: str
    title: str
    explanation: str
    source_document_ids: list[str] = Field(default_factory=list)
    hours_per_week: float = Field(0, ge=0, le=200)
    priority: Priority = Priority.media
    repetitive_activities: int = 0
    automation_id: str | None = None
    builder_prompt: str | None = None  # Builder-ready brief written by the Explorer (step 6 → step 7)


class StepView(HuskModel):
    step: DiscoveryStepId
    label: str
    status: StepStatus


class DiscoveryRunView(HuskModel):
    run: DiscoveryRun
    steps: list[StepView]
    studio_url: str | None = None


class DiscoveryReport(HuskModel):
    run_id: str
    documents_analyzed: int
    opportunities: int
    repetitive_activities: int
    total_hours_per_week: float
    items: list[Opportunity]
    usage: dict[str, Any] = Field(default_factory=dict)  # {steps: {step: usage}, by_model, total}


class DiscoveryState(StrEnum):
    """Sidebar badge: DA FARE / IN CORSO / FATTA."""

    da_fare = "da_fare"
    in_corso = "in_corso"
    fatta = "fatta"


class DiscoveryEvent(HuskModel):
    run_id: str
    type: str  # step_started | step_done | progress | log | completed | failed
    step: DiscoveryStepId | None = None
    progress: float = 0.0
    key: str | None = None  # i18n message key, rendered by the UI
    params: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)
