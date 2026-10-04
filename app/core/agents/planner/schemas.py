"""Typed outputs the Planner asks the model for (compact field names: fewer output tokens)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BriefNote(BaseModel):
    title: str = Field(..., max_length=90)
    claim: str = Field(..., description="complete information, full sentences, numbers when present")
    freq: str | None = None
    dept: list[str] = Field(default_factory=list, max_length=3)
    hrs: float | None = Field(None, ge=0, le=60)
    doc: str = Field(..., description="document alias (D01…)")
    chunk: str | None = Field(None, description="passage id, if the evidence is a listed passage")
    quote: str | None = Field(None, max_length=200)


class Brief(BaseModel):
    title: str = Field(..., max_length=90)
    prompt: str = Field(..., max_length=900, description="Builder brief: trigger, inputs, steps, decisions, outputs")
    hrs: float = Field(..., ge=0, le=80)
    prio: Literal["alta", "media", "bassa"] = "media"
    notes: list[BriefNote] = Field(default_factory=list, max_length=6)
