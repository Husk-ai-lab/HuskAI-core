from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NeonModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)
    __table__: ClassVar[str]


# --- core ---------------------------------------------------------------------------
class CoreOrganization(NeonModel):
    """core.organizations — written by the Stripe webhook (and Husk's id upsert)."""

    __table__ = "core.organizations"
    id: str  # Clerk organisation id
    stripe_customer_id: str | None = None  # unique
    name: str | None = None
    created_at: datetime | None = None
    deleted_at: datetime | None = None


class CoreUser(NeonModel):
    """core.users — ids only, no email or name."""

    __table__ = "core.users"
    id: str  # Clerk user id
    org_id: str
    role: str
    created_at: datetime | None = None


# --- billing ------------------------------------------------------------------------
class BillingStatus(StrEnum):
    active = "active"
    past_due = "past_due"
    suspended = "suspended"


class BillingState(NeonModel):
    """billing.billing_state — Stripe webhook only."""

    __table__ = "billing.billing_state"
    org_id: str
    installation_paid_at: datetime | None = None
    status: BillingStatus
    grace_until: datetime | None = None
    last_stripe_event_id: str | None = None
    updated_at: datetime | None = None


class StripeEvent(NeonModel):
    """billing.stripe_events — webhook idempotency."""

    __table__ = "billing.stripe_events"
    event_id: str
    type: str
    received_at: datetime
    processed_at: datetime | None = None


# --- usage --------------------------------------------------------------------------
class TokensDaily(NeonModel):
    """usage.tokens_daily — gateway metering, kept forever. PK (org_id, user_id, day, model)."""

    __table__ = "usage.tokens_daily"
    org_id: str
    user_id: str
    day: date  # UTC
    model: str
    tokens_in: int
    tokens_out: int
    calls: int


class MeterOutbox(NeonModel):
    """usage.meter_outbox — per-call ledger, kept 13 months."""

    __table__ = "usage.meter_outbox"
    id: UUID  # idempotency key
    org_id: str
    user_id: str
    model: str
    tokens_in: int
    tokens_out: int
    created_at: datetime
    sent_at: datetime | None = None  # null = not yet sent


# --- runtime ------------------------------------------------------------------------
class RuntimeDeployment(NeonModel):
    """runtime.deployments — deploy endpoint."""

    __table__ = "runtime.deployments"
    id: UUID
    org_id: str
    automation_id: str
    fly_app: str
    deployed_by: str
    status: str
    created_at: datetime
    stopped_at: datetime | None = None


class RuntimeRun(NeonModel):
    """runtime.runs — run history, no content."""

    __table__ = "runtime.runs"
    id: UUID
    deployment_id: UUID
    started_at: datetime
    ended_at: datetime | None = None
    status: str
    tokens: int
    error_code: str | None = None


# --- ops ----------------------------------------------------------------------------
class Channel(StrEnum):
    stable = "stable"
    beta = "beta"


class Release(NeonModel):
    """ops.releases — release CI. PK (component, channel)."""

    __table__ = "ops.releases"
    component: str
    channel: Channel
    version: str
    min_version: str
    assets: dict[str, Any]  # jsonb · url, sha256, size
    published_at: datetime


class Maintenance(NeonModel):
    """ops.maintenance — admin."""

    __table__ = "ops.maintenance"
    id: UUID
    message_it: str
    message_en: str
    starts_at: datetime
    ends_at: datetime
    blocking: bool


# --- audit --------------------------------------------------------------------------
class AuditLog(NeonModel):
    """audit.log — admin and support actions."""

    __table__ = "audit.log"
    id: int  # bigserial
    org_id: str | None = None
    actor: str
    action: str
    at: datetime
