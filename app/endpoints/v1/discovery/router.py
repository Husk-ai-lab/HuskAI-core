"""Discovery: start / resume runs, live progress (SSE), report, harness memory."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.core.agents.orchestrator import DiscoveryOrchestrator
from app.core.services import Services
from app.data.models.discovery import (
    DiscoveryReport,
    DiscoveryRun,
    DiscoveryRunView,
    DiscoveryState,
    RunStatus,
    StepView,
)
from app.data.models.document import DocumentStatus
from app.data.models.knowledge import Fact, NetworkStatus
from app.endpoints.deps import CurrentUser, current_user, get_services, llm_ready, not_in_maintenance
from app.gateways.sqlite.repositories import DiscoveryRepo
from app.i18n import t
from app.i18n.errors import api_error, request_lang

router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])
ACTIVE = {RunStatus.running, RunStatus.pending, RunStatus.waiting_network}


def _view(services: Services, repo: DiscoveryRepo, run: DiscoveryRun, lang: str) -> DiscoveryRunView:
    steps = [StepView(step=s.step, label=t(f"discovery.steps.{s.step.value}", lang), status=s.status)
             for s in repo.steps(run.id)]
    if run.error_key:
        run.error = t(run.error_key, lang, **run.error_params)  # rendered for the caller, not stored
    studio = services.studio.run_url(run.trace_id) if run.status == RunStatus.failed else None
    return DiscoveryRunView(run=run, steps=steps, studio_url=studio)


class StateResponse(BaseModel):
    state: DiscoveryState
    documents_ready: int
    run: DiscoveryRunView | None = None
    last_completed_run_id: str | None = None


@router.get("/state", response_model=StateResponse)
def state(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services),
          lang: str = Depends(request_lang)) -> StateResponse:
    repo = DiscoveryRepo(services.db, cu.org_id)
    latest = repo.latest()
    done = repo.latest_completed()
    ready = sum(1 for d in services.knowledge(cu.org_id).docs.list() if d.status == DocumentStatus.ingested)
    if latest and latest.status in ACTIVE:
        st = DiscoveryState.in_corso
    elif done:
        st = DiscoveryState.fatta
    else:
        st = DiscoveryState.da_fare
    return StateResponse(
        state=st, documents_ready=ready, run=_view(services, repo, latest, lang) if latest else None,
        last_completed_run_id=done.id if done else None,
    )


def _start(services: Services, cu: CurrentUser, run_id: str) -> None:
    ctx = services.agent_context(cu.org_id, cu.id)
    orch = DiscoveryOrchestrator(ctx, services.events, lambda: services.maintenance.blocking)
    services.spawn(f"discovery:{cu.org_id}", orch.execute(run_id))


@router.post("/runs", response_model=DiscoveryRunView, dependencies=[Depends(llm_ready), Depends(not_in_maintenance)])
async def start(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services),
                lang: str = Depends(request_lang)) -> DiscoveryRunView:
    task = services.tasks.get(f"discovery:{cu.org_id}")
    if task and not task.done():
        api_error(409, "errors.discovery_running")
    ctx = services.agent_context(cu.org_id, cu.id)
    orch = DiscoveryOrchestrator(ctx, services.events)
    try:
        run = orch.create_run(cu.id)
    except Exception:
        api_error(400, "errors.discovery_no_documents")
    if not run.document_ids:
        api_error(400, "errors.discovery_no_documents")
    _start(services, cu, run.id)
    return _view(services, orch.runs, run, lang)


@router.post("/runs/{run_id}/resume", response_model=DiscoveryRunView,
             dependencies=[Depends(llm_ready), Depends(not_in_maintenance)])
async def resume(run_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services),
                 lang: str = Depends(request_lang)):
    repo = DiscoveryRepo(services.db, cu.org_id)
    run = repo.get(run_id)
    if not run:
        api_error(404, "errors.discovery_not_found")
    if run.status == RunStatus.completed:
        return _view(services, repo, run, lang)
    _start(services, cu, run.id)
    return _view(services, repo, repo.get(run_id), lang)


@router.get("/runs/{run_id}", response_model=DiscoveryRunView)
def get_run(run_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services),
            lang: str = Depends(request_lang)):
    repo = DiscoveryRepo(services.db, cu.org_id)
    run = repo.get(run_id)
    if not run:
        api_error(404, "errors.discovery_not_found")
    return _view(services, repo, run, lang)


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, cu: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)) -> EventSourceResponse:
    if not DiscoveryRepo(services.db, cu.org_id).get(run_id):
        api_error(404, "errors.discovery_not_found")
    channel = f"discovery:{run_id}"
    q = services.events.subscribe(channel)

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
                if ev["type"] in ("completed", "failed", "paused"):
                    break
        finally:
            services.events.unsubscribe(channel, q)

    return EventSourceResponse(gen())


@router.get("/runs/{run_id}/report", response_model=DiscoveryReport)
def report(run_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    ctx = services.agent_context(cu.org_id, cu.id)
    orch = DiscoveryOrchestrator(ctx, services.events)
    if not orch.runs.get(run_id):
        api_error(404, "errors.discovery_not_found")
    return orch.report(run_id)


@router.get("/runs/{run_id}/signals")
def signals(run_id: str, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    return DiscoveryRepo(services.db, cu.org_id).signals(run_id)


class NetworkView(BaseModel):
    network_id: str
    automation_id: str | None
    label: str
    document_ids: list[str]
    notes: int
    summary_markdown: str | None


class MemoryView(BaseModel):
    company_profile_markdown: str
    facts: list[Fact]
    max_facts: int
    fact_history: list[dict]
    networks: list[NetworkView]


@router.get("/memory", response_model=MemoryView)
def memory(cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services),
           lang: str = Depends(request_lang)) -> MemoryView:
    mem = services.memory(cu.org_id)
    nets = []
    for n in mem.repo.networks(status=NetworkStatus.kept):
        nets.append(NetworkView(
            network_id=n.id, automation_id=n.automation_id, label=n.label, document_ids=n.document_ids,
            notes=len(mem.networks.notes(n.id)),
            summary_markdown=mem.networks.summary(n.automation_id) if n.automation_id else None,
        ))
    prof = mem.profile.profile()
    return MemoryView(
        company_profile_markdown=prof.render_markdown(lang), facts=prof.facts, max_facts=prof.max_facts,
        fact_history=mem.repo.fact_history()[:50], networks=nets,
    )
