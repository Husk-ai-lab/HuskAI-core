from __future__ import annotations

from datetime import datetime

from app.data.models.common import HuskModel


class MaintenanceWindow(HuskModel):
    id: str
    message_it: str
    message_en: str
    starts_at: datetime
    ends_at: datetime
    blocking: bool


class MaintenanceStatus(HuskModel):
    """`active/blocking/message/...` describe the window that matters now (current, else next).
    `upcoming` lists every scheduled window that hasn't started yet, soonest first."""

    active: bool = False
    blocking: bool = False
    message_it: str | None = None
    message_en: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    source: str = "none"  # neon | cache | none
    checked_at: datetime | None = None
    upcoming: list[MaintenanceWindow] = []


class ReleaseNotice(HuskModel):
    current: str
    latest: str | None = None
    min_version: str | None = None
    update_available: bool = False
    update_required: bool = False


class StudioStatus(HuskModel):
    alive: bool = False
    ui_url: str | None = None
    otlp: bool = False
    mcp: bool = False
    mcp_tools: list[str] = []
    checked_at: datetime | None = None


class SystemStatus(HuskModel):
    version: str
    llm_configured: bool
    llm_model: str | None
    zitadel_configured: bool
    neon_configured: bool
    maintenance: MaintenanceStatus
    release: ReleaseNotice
    studio: StudioStatus
    data_dir: str
