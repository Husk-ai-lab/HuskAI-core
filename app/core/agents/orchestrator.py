"""Husk Op: the coordinating agent of a discovery run.

Seven visible steps, each checkpointed in SQLite (discovery_steps + agent_checkpoints):
a crash or a dropped connection resumes from the last finished step — and inside the long
steps, from the last finished document. Explorer does the research, Builder drafts the
automations. When a step fails and a local Husk Studio is alive, its MCP tools are asked
for a diagnosis and the step is retried once with it (self-heal).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.agents.builder.agent import BuilderAgent
from app.core.agents.explorer.agent import ExplorerAgent
from app.core.agents.explorer.summarizer import Summarizer
from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.self_heal import with_self_heal
from app.core.agents.planner.agent import PlannerAgent
from app.core.events import EventHub
from app.core.metering import Meter, metered
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
from app.data.models.knowledge import DocumentCard, DocumentSignal, Network, NetworkStatus, NoteKind
from app.gateways.sqlite.repositories import AutomationRepo, CardRepo, CheckpointRepo, DiscoveryRepo
from app.gateways.studio.tracing import current_trace_id, exporting, span
from app.helpers.ids import new_id
from app.helpers.logging import get_logger
from app.helpers.text import truncate
from app.helpers.time import utcnow
from app.i18n import t
from app.i18n.errors import LocalizedError

log = get_logger("husk_op")

NETWORK_RETRY_S = 30
_BACKGROUND: set[asyncio.Task] = set()  # background card refreshes (kept referenced)
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
    def __init__(self, ctx: AgentContext, events: EventHub, is_blocked: Callable[[], bool] = lambda: False,
                 on_draft: Callable[[Automation], None] | None = None):
        self.ctx = ctx
        self.events = events
        self.is_blocked = is_blocked
        self.on_draft = on_draft  # e.g. report a connector the new draft needs but is not set up
        self.runs = DiscoveryRepo(ctx.db, ctx.org_id)
        self.ckpt = CheckpointRepo(ctx.db, ctx.org_id)
        self.automations = AutomationRepo(ctx.db, ctx.org_id)
        self.explorer = ExplorerAgent(ctx)
        self.builder = BuilderAgent(ctx)
        self.planner = PlannerAgent(ctx)

    # --- public -------------------------------------------------------------------------------
    def create_run(self, user_id: str) -> DiscoveryRun:
        docs = [d.id for d in self.ctx.knowledge.docs.list() if d.status == DocumentStatus.ingested]
        run = DiscoveryRun(id=new_id("run"), org_id=self.ctx.org_id, started_by=user_id, document_ids=docs)
        return self.runs.create(run)

    async def execute(self, run_id: str, until: DiscoveryStepId | None = None) -> DiscoveryRun:
        """Run (or resume) all steps; `until` stops after that step, leaving the run paused (benchmarks, gates)."""
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        run.status, run.error = RunStatus.running, None
        self.runs.update(run)
        self.events.clear(f"discovery:{run.id}")  # a resume must not replay the failed attempt (and its terminal event)
        with span("husk.discovery.run", **{"husk.run_id": run.id, "husk.org_id": run.org_id}) as trace_id:
            run.trace_id = run.trace_id or trace_id
            run.traced = run.traced or exporting()
            self.runs.update(run)
            try:
                for step_id in STEP_ORDER:
                    step = next(s for s in self.runs.steps(run.id) if s.step == step_id)
                    if step.status == StepStatus.done:
                        continue
                    await self._run_step(run, step)
                    if until is not None and step_id == until:
                        run.status = RunStatus.paused
                        self.runs.update(run)
                        return run
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
            usage=self.usage(run_id),
        )

    def usage(self, run_id: str) -> dict[str, Any]:
        """Per-step and per-model calls/tokens, summed from each step's stored meter."""
        steps: dict[str, Any] = {}
        total = Meter()
        for s in self.runs.steps(run_id):
            u = (s.output or {}).get("usage")
            if not u:
                continue
            steps[s.step.value] = u
            for model, m in u.get("by_model", {}).items():
                agg = total.by_model.setdefault(model, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
                for k in agg:
                    agg[k] += m.get(k, 0)
        return {"steps": steps, **total.as_dict()}

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
            run.current_step, run.progress, run.status = step.step, max(run.progress, idx / len(STEP_ORDER)), RunStatus.running
            self.runs.update(run)
            step.status, step.started_at, step.attempts = StepStatus.running, utcnow(), step.attempts + 1
            self.runs.save_step(step)
            self._emit(run, "step_started", step.step, key=f"discovery.steps.{step.step.value}")
            try:
                meter = Meter()
                with span(f"husk.discovery.{step.step.value}", **{"husk.run_id": run.id}), metered(meter):
                    output = await with_self_heal(
                        lambda hint: handler(run, hint), self.ctx.studio, current_trace_id
                    )
                output = {**output, "usage": meter.as_dict()}  # calls + tokens per model, this step
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
                step.output = {**(step.output or {}), "usage": meter.as_dict()}  # what the failed attempt cost
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
        p = max(run.progress, (idx + (done / max(total, 1))) / len(STEP_ORDER))  # never backwards, even on a retry
        run.progress = p
        self.runs.update(run)
        self._emit(run, "progress", step, progress=p, key=key, params={"done": done, "total": total, **params})

    def _reading(self, run: DiscoveryRun, finished: int, total: int) -> None:
        """Parallel summaries are one counter for the user, never one line per document."""
        self._sub_progress(run, DiscoveryStepId.extract, finished, total, "discovery.event.reading")

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

    # 3 — document cards (spreadsheets: computed facts, no AI; text: summary model), N at a time
    async def _step_extract(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        done = {s.document_id for s in self.runs.signals(run.id)}
        docs = [d for d in self.ctx.knowledge.docs.list() if d.id in run.document_ids]
        summarizer = Summarizer(self.ctx)
        gate = asyncio.Semaphore(max(1, self.ctx.settings.discovery_concurrency))
        built: list[str] = []
        timings: dict[str, dict[str, Any]] = {}
        finished = len(done)

        async def one(doc) -> None:  # type: ignore[no-untyped-def]
            nonlocal finished
            async with gate:
                started = time.perf_counter()
                card, fresh = await summarizer.card_for(doc)
                timings[doc.id] = {"file": doc.filename, "kind": card.kind.value, "model": card.model,
                                   "seconds": round(time.perf_counter() - started, 2), "fresh": fresh}
                if fresh:
                    built.append(doc.id)
                self.runs.save_signals([DocumentSignal(  # per-document checkpoint (ranking UI + report)
                    org_id=self.ctx.org_id, run_id=run.id, document_id=doc.id, score=card.signal,
                    heuristic_score=card.signal if card.model is None else 0, llm_score=card.signal if card.model else None,
                    doc_type=card.doc_type, department=card.department, reasons=card.reasons[:6])])
                finished += 1
                self._reading(run, finished, len(docs))

        self._reading(run, finished, len(docs))
        tasks = [asyncio.ensure_future(one(d)) for d in docs if d.id not in done]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            # one document failed (or the run was cancelled): stop the others NOW. Left running, they would keep
            # emitting on this attempt's counter while the retry starts its own, and the number would jump.
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        # documents that hit the deadline: rebuild their full card in the background, for the next run
        late = []
        for doc in docs:
            if timings.get(doc.id, {}).get("model") == "fallback" and not doc.kind.is_tabular:
                task = asyncio.create_task(summarizer.refresh(doc))
                _BACKGROUND.add(task)
                task.add_done_callback(_BACKGROUND.discard)
                late.append(task)
        if late:  # give late summaries a little more time: the Explorer then reads full cards for them
            landed, _ = await asyncio.wait(late, timeout=self.ctx.settings.summary_late_wait_s)
            for doc in docs:
                if doc.id in timings and timings[doc.id]["model"] == "fallback":
                    timings[doc.id]["late_refreshed"] = any(t.done() for t in landed)
        ranked = self.runs.signals(run.id)
        return {"cards": len(docs), "built": len(built), "cached": len(docs) - len(built) - len(done), "timings": timings,
                "ranking": [{"document_id": s.document_id, "score": s.score} for s in ranked]}

    # 4 — link the documents with the decision model (doc_related → relation_type → components)
    async def _step_repetitive(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        state = self._restore(run, "repetitive") or {}
        if "networks" in state:
            return state
        cards = CardRepo(self.ctx.db, self.ctx.org_id).list(run.document_ids)
        self._sub_progress(run, DiscoveryStepId.repetitive, 0, 1, "discovery.event.linking", count=len(cards))
        state = await self.explorer.link_documents(cards, run.id)
        self._checkpoint(run, "repetitive", state)
        return state

    def _explored(self, run: DiscoveryRun) -> tuple[dict[str, Any], dict[str, str], list[DocumentCard]]:
        """Step 4's networks (by N id), alias → document id, and the run's cards."""
        state = self._restore(run, "repetitive") or {}
        cards = CardRepo(self.ctx.db, self.ctx.org_id).list(run.document_ids)
        return state.get("networks", {}), state.get("aliases", {}), cards

    # 5 — assess each network with the decision model: repetitive? hours? which documents to re-read?
    async def _step_handoffs(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        networks, _, cards = self._explored(run)
        if networks:
            self._sub_progress(run, DiscoveryStepId.handoffs, 0, 1, "discovery.event.assessing", count=len(networks))
        assessment = await self.explorer.assess(networks, cards) if networks else {}
        gate = self.ctx.settings.gate_repetitive
        ranked = sorted((nid for nid, a in assessment.items() if a["repetitive"] >= gate),
                        key=lambda nid: -assessment[nid]["hours_per_week"])
        kept = ranked[: self.ctx.settings.discovery_max_opportunities]
        out = {"assessment": assessment, "kept": kept}
        self._checkpoint(run, "assessment", out)
        return out

    # 6 — briefs (LLM, low) for the kept networks, re-reading only the flagged documents → opportunities
    async def _step_impact(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        networks, alias_doc, card_list = self._explored(run)
        cards = {c.document_id: c for c in card_list}
        alias_of = {d: a for a, d in alias_doc.items()}
        state = self._restore(run, "assessment") or {"assessment": {}, "kept": []}
        assessment, kept_ids = state["assessment"], state["kept"]
        selected: dict[str, tuple[Network, list[str]]] = {}
        for nid in kept_ids:
            net = self.ctx.memory.networks.get(networks[nid]["net_id"])
            if net:
                selected[nid] = (net, [alias_doc[a] for a in assessment[nid]["deeper"] if a in alias_doc])
        if selected:
            self._sub_progress(run, DiscoveryStepId.impact, 0, 1, "discovery.event.briefing", count=len(selected))
        briefs = await self.planner.briefs(selected, cards, alias_of, hint)
        opps, kept = [], set()
        for nid, (net, _) in selected.items():
            a = assessment[nid]
            brief = briefs.get(nid)
            if brief:
                self.planner.refresh_notes(net, brief, alias_doc)
            notes = self.ctx.memory.networks.notes(net.id)
            prompt = brief.prompt if brief else "\n".join(n.claim for n in notes)
            hours = a["hours_per_week"]  # from the decision model
            prio = "alta" if hours > 5 else "media" if hours >= 2 else "bassa"
            kept.add(net.id)
            opps.append(Opportunity(
                id=new_id("opp"), org_id=self.ctx.org_id, run_id=run.id, network_id=net.id,
                title=brief.title if brief else net.label, explanation=truncate(prompt, 500),
                source_document_ids=net.document_ids, hours_per_week=hours, priority=Priority(prio),
                repetitive_activities=sum(1 for n in notes if n.kind != NoteKind.entity), builder_prompt=prompt,
            ))
        self.ctx.memory.networks.discard_others(run.id, kept)
        self.runs.save_opportunities(run.id, opps)
        return {"opportunities": [o.id for o in opps], "briefed": list(briefs)}

    # 7 — Builders in parallel (reasoning medium: the Builder is the agent the user talks to)
    async def _step_map(self, run: DiscoveryRun, hint: str | None) -> dict[str, Any]:
        self._retire_untouched_drafts(run)
        opps = self.runs.opportunities(run.id)
        gate = asyncio.Semaphore(max(1, len(opps), self.ctx.settings.builder_concurrency))  # all drafts at once
        failed: list[str] = []
        lock = asyncio.Lock()
        done = 0
        self._sub_progress(run, DiscoveryStepId.map, 0, len(opps), "discovery.event.drafting")

        async def one(opp: Opportunity) -> str | None:
            nonlocal done
            if opp.automation_id and self.automations.get(opp.automation_id):
                return opp.automation_id
            net = self.ctx.memory.networks.get(opp.network_id)
            if net is None:
                return None
            async with gate:
                try:
                    draft, graph = await self.builder.draft(opp, net, hint)
                except Exception as exc:  # one invalid draft never fails the run: the opportunity stays, retryable
                    log.warning("draft skipped opp=%s err=%s", opp.id, type(exc).__name__)
                    failed.append(opp.id)
                    return None
                auto = Automation(
                    id=new_id("auto"), org_id=self.ctx.org_id, title=draft.title or opp.title,
                    description=draft.description or opp.explanation, status=AutomationStatus.bozza, graph=graph,
                    source_document_ids=net.document_ids, hours_per_week=opp.hours_per_week,
                    priority=opp.priority.value, network_id=net.id, run_id=run.id,
                )
                self.automations.save(auto, change_note=t("agent.change_note_draft", self.ctx.language), author="builder")
                if self.on_draft:
                    self.on_draft(auto)
                self.ctx.memory.networks.keep(net.id, auto.id)
                self.ctx.memory.networks.write_summary(auto.id, net.id, await self.builder.summarize(auto.title, net))
            async with lock:  # per-automation checkpoint
                opp.automation_id = auto.id
                done += 1
                self.runs.save_opportunities(run.id, opps)
                self._sub_progress(run, DiscoveryStepId.map, done, len(opps), "discovery.event.drafting")
            return auto.id

        created = [a for a in await asyncio.gather(*(one(o) for o in opps)) if a]
        return {"automations": created, "skipped": failed}

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
