"""Automations: list, open, Prova (local test run), Carica / Attiva (local only), history.

No manual creation or manual editing in this phase: changes go through the builder chat.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.automations.service import AutomationNotFound, AutomationService
from app.core.services import Services
from app.data.models.automation import (
    Automation,
    AutomationRun,
    AutomationStatus,
    AutomationVersion,
    RunTrigger,
)
from app.endpoints.deps import CurrentUser, current_user, get_services, llm_ready, not_in_maintenance
from app.i18n.errors import api_error

router = APIRouter(prefix="/api/v1/automations", tags=["automations"])


def _svc(cu: CurrentUser, services: Services) -> AutomationService:
    return AutomationService(services, cu.org_id, cu.id)


class AutomationView(BaseModel):
    automation: Automation
    next_run: str | None = None
    last_run: AutomationRun | None = None


@router.get("", response_model=list[Automation])
def list_automations(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    return _svc(cu, services).repo.list()


@router.get("/{automation_id}", response_model=AutomationView)
def get_automation(automation_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    svc = _svc(cu, services)
    try:
        a = svc.get(automation_id)
    except AutomationNotFound:
        api_error(404, "errors.automation_not_found")
    runs = svc.repo.runs(automation_id, limit=1)
    nxt = services.scheduler.next_run(a.id) if services.scheduler else None
    return AutomationView(automation=a, next_run=nxt, last_run=runs[0] if runs else None)


class StatusChange(BaseModel):
    status: AutomationStatus


@router.post("/{automation_id}/status", response_model=Automation)
async def change_status(automation_id: str, body: StatusChange, cu: CurrentUser = Depends(current_user),
                  services: Services = Depends(get_services)):
    if body.status == AutomationStatus.attiva and services.maintenance.blocking:
        api_error(503, "errors.maintenance_retry_later")
    try:
        return _svc(cu, services).set_status(automation_id, body.status)
    except AutomationNotFound:
        api_error(404, "errors.automation_not_found")


class TestRunResponse(BaseModel):
    run: AutomationRun
    studio_url: str | None = None


@router.post("/{automation_id}/test", response_model=TestRunResponse,
             dependencies=[Depends(llm_ready), Depends(not_in_maintenance)])
async def test_run(automation_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    try:
        run = await _svc(cu, services).run(automation_id, RunTrigger.prova)
    except AutomationNotFound:
        api_error(404, "errors.automation_not_found")
    studio = services.studio.run_url(run.trace_id) if run.status.value == "failed" else None
    return TestRunResponse(run=run, studio_url=studio)


@router.get("/{automation_id}/runs", response_model=list[AutomationRun])
def runs(automation_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    return _svc(cu, services).repo.runs(automation_id)


@router.get("/{automation_id}/versions", response_model=list[AutomationVersion])
def versions(automation_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    return _svc(cu, services).repo.versions(automation_id)


@router.post("/{automation_id}/undo", response_model=Automation)
async def undo(automation_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    try:
        return _svc(cu, services).undo(automation_id)
    except AutomationNotFound:
        api_error(404, "errors.automation_not_found")


@router.delete("/{automation_id}")
async def delete(automation_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> dict:
    if services.scheduler:
        services.scheduler.remove(automation_id)
    _svc(cu, services).repo.delete(automation_id)
    return {"ok": True}


@router.get("/{automation_id}/memory")
def memory(automation_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)) -> dict:
    mem = services.memory(cu.org_id)
    net = mem.repo.network_for_automation(automation_id)
    return {
        "summary_markdown": mem.networks.summary(automation_id),
        "notes_markdown": mem.networks.render(net) if net else None,
    }
