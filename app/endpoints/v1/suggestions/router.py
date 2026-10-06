"""Suggestions ("Suggerimenti"): what Husk noticed after documents were added or removed.

Listing and status changes are plain reads/writes. `apply` only acts directly for kinds that do not
touch an existing automation (new draft, memory acknowledgement); modify suggestions are applied
through the Builder chat (`suggestion_id` on the message) and always end in a proposal.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.core.services import Services
from app.core.suggestions.engine import SuggestionEngine, channel
from app.data.models.suggestion import (
    OPEN_STATUSES,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    SuggestionSummary,
)
from app.endpoints.deps import CurrentUser, current_user, get_services, llm_ready, not_in_maintenance
from app.gateways.sqlite.repositories import SuggestionRepo
from app.helpers.logging import get_logger
from app.i18n.errors import api_error

log = get_logger("suggestions")

router = APIRouter(prefix="/api/v1/suggestions", tags=["suggestions"])

FILTERS = {
    "open": OPEN_STATUSES,
    "new": (SuggestionStatus.new,),
    "applied": (SuggestionStatus.applied,),
    "dismissed": (SuggestionStatus.dismissed,),
    "all": None,
}


@router.get("", response_model=list[Suggestion])
def list_suggestions(status: str = "open", cu: CurrentUser = Depends(current_user),
                     services: Services = Depends(get_services)) -> list[Suggestion]:
    if status not in FILTERS:
        api_error(400, "errors.suggestion_bad_filter")
    return SuggestionRepo(services.db, cu.org_id).list(FILTERS[status])


@router.get("/summary", response_model=SuggestionSummary)
def summary(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> SuggestionSummary:
    repo = SuggestionRepo(services.db, cu.org_id)
    unread = repo.count(SuggestionStatus.new)
    return SuggestionSummary(unread=unread, open=unread + repo.count(SuggestionStatus.seen),
                             processing=services.suggestions.is_processing(cu.org_id))


@router.get("/events")
async def events(request: Request, cu: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)) -> EventSourceResponse:
    ch = channel(cu.org_id)
    q = services.events.subscribe(ch, replay=False)  # no replay: old events must not re-trigger snackbars

    async def gen():  # type: ignore[no-untyped-def]
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}
        finally:
            services.events.unsubscribe(ch, q)

    return EventSourceResponse(gen())


@router.get("/{suggestion_id}", response_model=Suggestion)
def get_suggestion(suggestion_id: str, cu: CurrentUser = Depends(current_user),
                   services: Services = Depends(get_services)) -> Suggestion:
    s = SuggestionRepo(services.db, cu.org_id).get(suggestion_id)
    if s is None:
        api_error(404, "errors.suggestion_not_found")
    return s


class SeenRequest(BaseModel):
    ids: list[str]


@router.post("/seen")
def mark_seen(body: SeenRequest, cu: CurrentUser = Depends(current_user),
              services: Services = Depends(get_services)) -> dict:
    repo = SuggestionRepo(services.db, cu.org_id)
    n = 0
    for sid in body.ids[:200]:
        s = repo.get(sid)
        if s and s.status == SuggestionStatus.new:
            repo.set_status(sid, SuggestionStatus.seen)
            n += 1
    return {"updated": n}


@router.post("/{suggestion_id}/dismiss", response_model=Suggestion)
def dismiss(suggestion_id: str, cu: CurrentUser = Depends(current_user),
            services: Services = Depends(get_services)) -> Suggestion:
    s = SuggestionRepo(services.db, cu.org_id).set_status(suggestion_id, SuggestionStatus.dismissed)
    if s is None:
        api_error(404, "errors.suggestion_not_found")
    return s


class ApplyResult(BaseModel):
    suggestion: Suggestion
    automation_id: str | None = None


def _get_open(services: Services, org_id: str, suggestion_id: str) -> Suggestion:
    s = SuggestionRepo(services.db, org_id).get(suggestion_id)
    if s is None:
        api_error(404, "errors.suggestion_not_found")
    if s.status not in OPEN_STATUSES:
        api_error(409, "errors.suggestion_closed")
    return s


@router.post("/{suggestion_id}/apply", response_model=ApplyResult)
async def apply(suggestion_id: str, cu: CurrentUser = Depends(current_user),
                services: Services = Depends(get_services)) -> ApplyResult:
    s = _get_open(services, cu.org_id, suggestion_id)
    repo = SuggestionRepo(services.db, cu.org_id)
    if s.kind in (SuggestionKind.memory_update, SuggestionKind.run_discovery, SuggestionKind.configure_connector):
        return ApplyResult(suggestion=repo.set_status(s.id, SuggestionStatus.applied))  # type: ignore[arg-type]
    if s.kind == SuggestionKind.new_automation:
        llm_ready(cu, services)
        not_in_maintenance(services)
        try:
            auto = await SuggestionEngine(services, cu.org_id, cu.id).create_draft(s)
        except Exception as exc:
            log.warning("suggestion draft failed: %s", type(exc).__name__)
            api_error(502, "errors.suggestion_draft_failed", detail=str(exc)[:200])
        return ApplyResult(suggestion=repo.get(s.id), automation_id=auto.id)  # type: ignore[arg-type]
    # modify_automation / source_deleted change an existing automation: only through the Builder chat
    api_error(400, "errors.suggestion_use_chat")
