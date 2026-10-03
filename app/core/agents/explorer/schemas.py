"""Typed outputs the explorer asks the LLM for."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.data.models.knowledge import FactCategory, NoteKind


class FactDraft(BaseModel):
    text: str = Field(..., max_length=300, description="stable fact about the company (never people's names)")
    category: FactCategory = FactCategory.process
    importance: float = Field(0.5, ge=0, le=1)
    quote: str | None = Field(None, max_length=300)


class DocumentAnalysis(BaseModel):
    doc_type: str = Field(..., description="procedure, bill of materials, order, production report, work instruction, other")
    department: str = Field(..., description="main department, in the user's language (e.g. Quality, Engineering, Production, Purchasing, Warehouse)")
    summary: str = Field(..., max_length=600)
    automation_signal: float = Field(..., ge=0, le=1, description="how much repetitive, automatable work the document describes")
    signal_reasons: list[str] = Field(default_factory=list, max_length=6)
    facts: list[FactDraft] = Field(default_factory=list, max_length=6)


class NoteDraft(BaseModel):
    title: str
    claim: str = Field(..., description="complete information, full sentences, with numbers when present")
    kind: NoteKind = NoteKind.task
    frequency: str | None = None
    actors: list[str] = Field(default_factory=list)
    hours_per_week: float | None = Field(None, ge=0, le=80)
    evidence_document_id: str
    evidence_chunk_id: str | None = None
    quote: str | None = Field(None, max_length=300)
    related_titles: list[str] = Field(default_factory=list)


class NotesExtraction(BaseModel):
    notes: list[NoteDraft] = Field(default_factory=list, max_length=10)


class LinkDraft(BaseModel):
    note_a: str
    note_b: str
    reason: str = ""


class HandoffMap(BaseModel):
    links: list[LinkDraft] = Field(default_factory=list, max_length=40)


class OpportunityDraft(BaseModel):
    title: str = Field(..., max_length=80, description="short automation name, in the user's language")
    explanation: str = Field(..., max_length=500, description="plain explanation: what is done by hand today and what changes")
    note_ids: list[str] = Field(..., min_length=1)
    hours_per_week: float = Field(..., ge=0, le=80)
    priority: str = Field("media", description="alta (high), media (medium) or bassa (low)")


class OpportunityMap(BaseModel):
    opportunities: list[OpportunityDraft] = Field(default_factory=list, max_length=8)


class SuggestionEvidenceDraft(BaseModel):
    chunk_id: str = Field(..., description="id of a passage of the NEW document, exactly as listed")
    quote: str = Field(..., max_length=300, description="short verbatim quote from that passage")


class SuggestionDraft(BaseModel):
    kind: str = Field(..., description="modify_automation (an existing automation should change) or new_automation")
    automation_id: str | None = Field(None, description="required for modify_automation: one of the listed ids")
    title: str = Field(..., max_length=120, description="short, in the user's language")
    body: str = Field(..., max_length=700, description="what the document says and why it matters, plain language")
    prompt: str = Field(..., max_length=900, description="the request to give the Builder, in the user's language, "
                        "naming the document; for new_automation, what the automation should do")
    evidence: list[SuggestionEvidenceDraft] = Field(..., min_length=1, max_length=4)
    confidence: float = Field(..., ge=0, le=1)
    hours_per_week: float | None = Field(None, ge=0, le=80)


class SuggestionMap(BaseModel):
    suggestions: list[SuggestionDraft] = Field(default_factory=list, max_length=4)
