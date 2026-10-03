"""Husk Op: the coordinating agent of a discovery run.

Seven visible steps, each checkpointed in SQLite (discovery_steps + agent_checkpoints):
a crash or a dropped connection resumes from the last finished step — and inside the long
steps, from the last finished document. Explorer does the research, Builder drafts the
automations. When a step fails and a local Husk Studio is alive, its MCP tools are asked
for a diagnosis and the step is retried once with it (self-heal).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.agents.builder.agent import BuilderAgent
from app.core.agents.explorer.agent import ExplorerAgent
from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.self_heal import with_self_heal
from app.core.events import EventHub
from app.data.models.agent import AgentCheckpoint
from app.data.models.automation import Automation, AutomationStatus
from app.data.models.discovery import (
    STEP_ORDER,
    DiscoveryReport,
    DiscoveryRun,
    DiscoveryStep,
    DiscoveryStepId,
    Opportunity,
    Priority,
    RunStatus,
    StepStatus,
)
from app.data.models.document import DocumentStatus
from app.data.models.knowledge import NetworkStatus, NoteKind
from app.gateways.sqlite.repositories import AutomationRepo, CheckpointRepo, DiscoveryRepo
from app.gateways.studio.tracing import current_trace_id, span
from app.helpers.ids import new_id
from app.helpers.logging import get_logger
from app.helpers.time import utcnow
from app.i18n import t
from app.i18n.errors import LocalizedError

log = get_logger("husk_op")

NETWORK_RETRY_S = 30
NETWORK_MAX_WAITS = 40


class MaintenanceActive(LocalizedError):
    status = 503

    def __init__(self) -> None:
        super().__init__("errors.run_paused_maintenance")


def _is_network_error(exc: BaseException) -> bool:
    names = {type(e).__name__ for e in _chain(exc)}
    return bool(
        names & {"ConnectError", "ConnectTimeout", "APIConnectionError", "APITimeoutError", "ReadTimeout",
                 "RemoteProtocolError", "ClientConnectorError"}
    ) or any(isinstance(e, (httpx.TransportError, ConnectionError)) for e in _chain(exc))


def _chain(exc: BaseException):  # type: ignore[no-untyped-def]
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__  # type: ignore[assignment]


class DiscoveryOrchestrator:
    def __init__(self, ctx: AgentContext, events: EventHub, is_blocked: Callable[[], bool] = lambda: False):
        self.ctx = ctx
        self.events = events
        self.is_blocked = is_blocked
        self.runs = DiscoveryRepo(ctx.db, ctx.org_id)
        self.ckpt = CheckpointRepo(ctx.db, ctx.org_id)
        self.automations = AutomationRepo(ctx.db, ctx.org_id)
        self.explorer = ExplorerAgent(ctx)
        self.builder = BuilderAgent(ctx)

    # --- public -------------------------------------------------------------------------------
    def create_run(self, user_id: str) -> DiscoveryRun:
        docs = [d.id for d in self.ctx.knowledge.docs.list() if d.status == DocumentStatus.ingested]
        run = DiscoveryRun(id=new_id("run"), org_id=self.ctx.org_id, started_by=user_id, document_ids=docs)
        return self.runs.create(run)

    async def execute(self, run_id: str) -> DiscoveryRun:
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        run.status, run.error = RunStatus.running, None
        self.runs.update(run)
        with span("husk.discovery.run", **{"husk.run_id": run.id, "husk.org_id": run.org_id}) as trace_id:
            run.trace_id = run.trace_id or trace_id
            self.runs.update(run)
            try:
                for step_id in STEP_ORDER:
                    step = next(s for s in self.runs.steps(run.id) if s.step == step_id)
                    if step.status == StepStatus.done:
                        continue
                    await self._run_step(run, step)
                run.status, run.progress, run.current_step = RunStatus.completed, 1.0, None
                run.finished_at = utcnow()
                self.runs.update(run)
                self._emit(run, "completed", progress=1.0, key="discovery.event.completed")
            except MaintenanceActive as exc:
                run.status = RunStatus.paused
                self._set_error(run, exc)
                self.runs.update(run)
                self._emit(run, "paused", key=exc.key)
            except asyncio.CancelledError:
                run.status = RunStatus.paused
                self.runs.update(run)
                raise
            except Exception as exc:
                log.exception("discovery failed run=%s", run.id)
                run.status = RunStatus.failed
                self._set_error(run, exc)
                self.runs.update(run)
                self._emit(run, "failed", key=run.error_key, params=run.error_params)
        return run

    def report(self, run_id: str) -> DiscoveryReport:
        run = self.runs.get(run_id)
        opps = self.runs.opportunities(run_id)
        activities = 0
        for net in self.ctx.memory.repo.networks(run_id=run_id, status=NetworkStatus.kept):
            activities += sum(1 for n in self.ctx.memory.networks.notes(net.id) if n.kind != NoteKind.entity)
        return DiscoveryReport(
            run_id=run_id,
            documents_analyzed=len(run.document_ids) if run else 0,
            opportunities=len(opps),
            repetitive_activities=activities,
            total_hours_per_week=round(sum(o.hours_per_week for o in opps), 1),
            items=opps,
        )

    # --- internals ----------------------------------------------------------------------------
    def _emit(self, run: DiscoveryRun, type_: str, step: DiscoveryStepId | None = None,
              progress: float | None = None, key: str | None = None, params: dict[str, Any] | None = None) -> None:
        """Events carry i18n keys + params; the UI renders them in the user's language."""
        self.events.publish(
            f"discovery:{run.id}",
            {"run_id": run.id, "type": type_, "step": step.value if step else None,
             "progress": run.progress if progress is None else progress, "key": key, "params": params or {},
             "at": utcnow().isoformat()},
        )

    @staticmethod
    def _set_error(run: DiscoveryRun, exc: BaseException) -> None:
        run.error_key, run.error_params = _error_key(exc)
        run.error = str(exc)[:400] or type(exc).__name__

    async def _run_step(self, run: DiscoveryRun, step: DiscoveryStep) -> None:
        idx = STEP_ORDER.index(step.step)
        handler: Callable[[DiscoveryRun, str | None], Awaitable[dict[str, Any]]] = getattr(self, f"_step_{step.step.value}")
        waits = 0
        while True:
            if self.is_blocked():
                raise MaintenanceActive()
            run.current_step, run.progress, run.status = step.step, idx / len(STEP_ORDER), RunStatus.running
            self.runs.update(run)
            step.status, step.started_at, step.attempts = StepStatus.running, utcnow(), step.attempts + 1
            self.runs.save_step(step)
            self._emit(run, "step_started", step.step, key=f"discovery.steps.{step.step.value}")
            try:
                with span(f"husk.discovery.{step.step.value}", **{"husk.run_id": run.id}):
                    output = await with_self_heal(
                        lambda hint: handler(run, hint), self.ctx.studio, current_trace_id
                    )
                break
            except MaintenanceActive:
                raise
            except Exception as exc:
                if _is_network_error(exc) and waits < NETWORK_MAX_WAITS:
                    waits += 1
                    run.status = RunStatus.waiting_network
                    self.runs.update(run)
                    step.status = StepStatus.pending
                    self.runs.save_step(step)
                    self._emit(run, "waiting_network", step.step, key="discovery.event.waiting_network")
                    await asyncio.sleep(NETWORK_RETRY_S)
                    continue
                step.status, step.error, step.finished_at = StepStatus.failed, str(exc)[:400], utcnow()
                self.runs.save_step(step)
                raise
        step.status, step.output, step.error, step.finished_at = StepStatus.done, output, None, utcnow()
        self.runs.save_step(step)
        run.progress = (idx + 1) / len(STEP_ORDER)
        self.runs.update(run)
        self._emit(run, "step_done", step.step)

    def _checkpoint(self, run: DiscoveryRun, step: str, state: dict[str, Any]) -> None:
        self.ckpt.save(AgentCheckpoint(id=new_id("ck"), org_id=self.ctx.org_id, run_id=run.id, agent="husk_op",
                                       step=step, state=state))

    def _restore(self, run: DiscoveryRun, step: str) -> dict[str, Any]:
        ck = self.ckpt.latest(run.id, "husk_op", step)
        return ck.state if ck else {}

    def _sub_progress(self, run: DiscoveryRun, step: DiscoveryStepId, done: int, total: int, key: str,
                      **params: Any) -> None:
        idx = STEP_ORDER.index(step)
        p = (idx + (done / max(total, 1))) / len(STEP_ORDER)
        run.progress = p
        self.runs.update(run)
        self._emit(run, "progress", step, progress=p, key=key, params=params)

    # 1
    async def _step_load(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        docs = self.ctx.knowledge.docs
        ready = [d for d in docs.list() if d.status == DocumentStatus.ingested]
        if not ready:
            raise LocalizedError("errors.discovery_no_documents")
        run.document_ids = [d.id for d in ready]
        with self.ctx.db.tx() as c:
            import json

            c.execute("UPDATE discovery_runs SET document_ids = ? WHERE id = ?", (json.dumps(run.document_ids), run.id))
        return {"documents": run.document_ids}

    # 2
    async def _step_knowledge_base(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        n = await asyncio.to_thread(self.ctx.knowledge.reindex)
        return {"chunks": n}

    # 3
    async def _step_extract(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        done = {s.document_id for s in self.runs.signals(run.id)}
        docs = [d for d in self.ctx.knowledge.docs.list() if d.id in run.document_ids]
        for i, doc in enumerate(docs):
            if doc.id in done:
                continue
            self._sub_progress(run, DiscoveryStepId.extract, i, len(docs), "discovery.event.analyzing", file=doc.filename)
            signal = await self.explorer.analyze(run.id, doc, hint)
            self.runs.save_signals([signal])  # per-document checkpoint
        ranked = self.runs.signals(run.id)
        return {"ranking": [{"document_id": s.document_id, "score": s.score} for s in ranked]}

    # 4
    async def _step_repetitive(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        state = self._restore(run, "repetitive") or {"done": {}, "order": []}
        ranked = self.runs.signals(run.id)
        top = [s for s in ranked if s.score >= 0.15][: self.ctx.settings.discovery_max_docs_deep] or ranked[:3]
        for i, sig in enumerate(top):
            if sig.document_id in state["done"]:
                continue
            doc = self.ctx.knowledge.docs.get(sig.document_id)
            label = doc.title if doc else sig.document_id
            self._sub_progress(run, DiscoveryStepId.repetitive, i, len(top), "discovery.event.researching", document=label)
            net = self.ctx.memory.networks.create(run.id, label, [sig.document_id])
            existing = "\n".join(
                f"- {n.id}: {n.title}"
                for nid in state["done"].values()
                for n in self.ctx.memory.networks.notes(nid)
            )[:4000]

            async def log_fn(msg: str, _label: str = label) -> None:
                self._emit(run, "log", DiscoveryStepId.repetitive, key="discovery.event.tool",
                           params={"document": _label, "tool": msg})

            await self.explorer.research(net, sig.document_id, existing, log_fn=log_fn, hint=hint)
            state["done"][sig.document_id] = net.id
            self._checkpoint(run, "repetitive", state)
        return {"networks": list(state["done"].values())}

    def _candidate_networks(self, run: DiscoveryRun):  # type: ignore[no-untyped-def]
        return self.ctx.memory.repo.networks(run_id=run.id, status=NetworkStatus.candidate)

    # 5
    async def _step_handoffs(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        links = await self.explorer.map_handoffs(self._candidate_networks(run))
        return {"links": links}

    # 6
    async def _step_impact(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        candidates = self._candidate_networks(run)
        proposal = await self.explorer.propose_opportunities(candidates, self.ctx.settings.discovery_max_opportunities)
        opps = []
        kept: set[str] = set()
        for o in proposal.opportunities:
            net = self.ctx.memory.networks.create(run.id, o.title)
            clones = self.ctx.memory.networks.clone_notes(o.note_ids, net.id)
            kept.add(net.id)
            net = self.ctx.memory.networks.get(net.id)
            prio = o.priority.lower() if o.priority.lower() in ("alta", "media", "bassa") else "media"
            opps.append(Opportunity(
                id=new_id("opp"), org_id=self.ctx.org_id, run_id=run.id, network_id=net.id, title=o.title,
                explanation=o.explanation, source_document_ids=net.document_ids, hours_per_week=o.hours_per_week,
                priority=Priority(prio), repetitive_activities=sum(1 for c in clones if c.kind != NoteKind.entity),
            ))
        # the kept per-opportunity networks replace the exploratory ones
        self.ctx.memory.networks.discard_others(run.id, kept)
        self.runs.save_opportunities(run.id, opps)
        return {"opportunities": [o.id for o in opps]}

    # 7
    async def _step_map(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        self._retire_untouched_drafts(run)
        opps = self.runs.opportunities(run.id)
        created = []
        for i, opp in enumerate(opps):
            if opp.automation_id and self.automations.get(opp.automation_id):
                created.append(opp.automation_id)
                continue
            self._sub_progress(run, DiscoveryStepId.map, i, len(opps), "discovery.event.drafting", title=opp.title)
            net = self.ctx.memory.networks.get(opp.network_id)
            if net is None:
                continue
            draft, graph = await self.builder.draft(opp, net, hint)
            auto = Automation(
                id=new_id("auto"), org_id=self.ctx.org_id, title=draft.title or opp.title,
                description=draft.description or opp.explanation, status=AutomationStatus.bozza, graph=graph,
                source_document_ids=net.document_ids, hours_per_week=opp.hours_per_week,
                priority=opp.priority.value, network_id=net.id, run_id=run.id,
            )
            self.automations.save(auto, change_note=t("agent.change_note_draft", self.ctx.language), author="builder")
            self.ctx.memory.networks.keep(net.id, auto.id)
            summary = await self.builder.summarize(auto.title, net)
            self.ctx.memory.networks.write_summary(auto.id, net.id, summary)
            opp.automation_id = auto.id
            self.runs.save_opportunities(run.id, opps)  # per-automation checkpoint
            created.append(auto.id)
        return {"automations": created}

    def _retire_untouched_drafts(self, run: DiscoveryRun) -> None:
        """'Analizza di nuovo': drafts from older runs that nobody touched are replaced."""
        for a in self.automations.list():
            if a.run_id and a.run_id != run.id and a.status == AutomationStatus.bozza and a.version <= 1:
                self.automations.delete(a.id)


def _error_key(exc: BaseException) -> tuple[str, dict[str, Any]]:
    """Localized errors keep their key; anything else becomes errors.generic with the detail."""
    if isinstance(exc, LocalizedError):
        return exc.key, {k: v for k, v in exc.params.items() if isinstance(v, (str, int, float, bool))}
    return "errors.generic", {"detail": (str(exc) or type(exc).__name__)[:300]}
