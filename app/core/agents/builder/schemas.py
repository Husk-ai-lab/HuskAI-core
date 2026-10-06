"""Typed drafts the builder asks the LLM for (kept flat: easier for models to fill)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.data.models.automation import (
    ActionType,
    AttachmentRole,
    ConditionOp,
    NotificationChannel,
    TriggerType,
)


class AttachmentDraft(BaseModel):
    role: AttachmentRole
    label: str = Field(..., max_length=60)
    subtitle: str | None = Field(None, max_length=60)
    document_ids: list[str] = Field(default_factory=list)
    tool: str | None = None


class TriggerDraft(BaseModel):
    type: TriggerType = TriggerType.schedule
    label: str = Field(..., max_length=80, description="e.g. 'Every day at 18:00' or 'New BOM revision', in the user's language")
    subtitle: str | None = Field(None, max_length=80)
    cron: str | None = Field(None, description="5-field cron for type=schedule, e.g. '0 18 * * 1-5'")
    folder: str | None = Field(None, description="watched folder for type=file")
    pattern: str | None = None


class StepDraft(BaseModel):
    ref: str = Field(..., description="short unique lowercase id, e.g. 'analysis'")
    kind: Literal["agent", "decision", "condition", "action", "notification"]
    label: str = Field(..., max_length=80)
    subtitle: str | None = Field(None, max_length=80)
    after: str = Field("trigger", description="ref of the previous step ('trigger' for the first)")
    branch: str | None = Field(None, description="for branches: 'vero'/'falso' or one of the decision options")
    instructions: str | None = Field(None, description="agent: what the AI agent must do")
    attachments: list[AttachmentDraft] = Field(default_factory=list, description="agent: model, memory, tools")
    question: str | None = Field(None, description="decision: multiple-choice question")
    options: list[str] | None = None
    field: str | None = Field(None, description="condition: state key, e.g. 'anomalies'")
    op: ConditionOp | None = None
    value: float | str | None = None
    action: ActionType | None = Field(None, description="action: write_file | write_xlsx | pdf_report | log | erp_update (anything that SENDS is kind 'notification')")
    target: str | None = Field(None, description="action: folder or ERP entity")
    template: str | None = Field(None, description="action: file/report body; notification: the message")
    channel: NotificationChannel | None = Field(None, description="notification: desktop | telegram | gmail")
    recipients: str | None = Field(None, description="notification by gmail: recipient address(es), comma-separated; never invent one")
    subject: str | None = Field(None, description="notification by gmail: subject")
    attach_report: bool = Field(False, description="notification: attach the PDF made by an earlier pdf_report step")


class FlowDraft(BaseModel):
    title: str = Field(..., max_length=80)
    description: str = Field(..., max_length=400)
    trigger: TriggerDraft
    steps: list[StepDraft] = Field(..., min_length=1, max_length=12)
