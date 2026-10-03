from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.helpers.time import utcnow


class HuskModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False, extra="ignore")


class OrgScoped(HuskModel):
    """Every persisted record belongs to one organisation (B2B multi-tenancy)."""

    org_id: str


class Timestamped(HuskModel):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
