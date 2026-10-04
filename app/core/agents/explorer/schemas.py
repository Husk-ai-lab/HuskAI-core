"""Typed outputs the explorer asks the LLM for."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.data.models.knowledge import FactCategory


class FactDraft(BaseModel):
    text: str = Field(..., max_length=300, description="stable fact about the company (never people's names)")
    category: FactCategory = FactCategory.process
    importance: float = Field(0.5, ge=0, le=1)
    quote: str | None = Field(None, max_length=300)


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


# --- document cards + confidence-driven exploration ------------------------------------------------


class SignalsDraft(BaseModel):
    rep: float = Field(..., ge=0, le=1, description="how much repetitive, automatable work it shows (0-1)")
    manual: bool = Field(False, description="work done by hand: re-typing, manual checks, copy-paste, emails")
    freq: str | None = Field(None, max_length=40, description="how often, e.g. daily, weekly, every order")
    dept: list[str] = Field(default_factory=list, max_length=4, description="departments/roles involved")
    vol: str | None = Field(None, max_length=60, description="volumes if stated, e.g. 26 invoices/week")
    sys: list[str] = Field(default_factory=list, max_length=4, description="systems/files used: ERP, Excel, email…")


class QuoteDraft(BaseModel):
    chunk_id: str = Field(..., description="passage id of THIS document, exactly as listed")
    quote: str = Field(..., max_length=200, description="short verbatim quote")


class CardDraft(BaseModel):
    sentence: str = Field(..., max_length=250, description="ONE sentence: what the document is + the repetitive work it shows")
    summary: str = Field(..., max_length=600, description="what is done, by whom, how often, with which data; manual steps")
    signals: SignalsDraft
    quotes: list[QuoteDraft] = Field(default_factory=list, max_length=3, description="key evidence, only if present")
    facts: list[FactDraft] = Field(default_factory=list, max_length=3)
