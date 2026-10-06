"""Automation use cases: status changes (Carica / Attiva), Prova, history, undo, proposals."""

from __future__ import annotations

from app.core.agents.builder.graph_ops import apply_ops
from app.core.automations.executor import AutomationExecutor
from app.data.models.agent import Proposal, ProposalStatus
from app.data.models.automation import Automation, AutomationRun, AutomationStatus, RunTrigger
from app.gateways.sqlite.repositories import AutomationRepo, ChatRepo


class AutomationNotFound(KeyError):
    pass


class AutomationService:
    def __init__(self, services, org_id: str, user_id: str):  # type: ignore[no-untyped-def]
        self.s = services
        self.org_id, self.user_id = org_id, user_id
        self.repo = AutomationRepo(services.db, org_id)

    def get(self, automation_id: str) -> Automation:
        a = self.repo.get(automation_id)
        if a is None:
            raise AutomationNotFound(automation_id)
        return a

    def set_status(self, automation_id: str, status: AutomationStatus) -> Automation:
        a = self.get(automation_id)
        a.status = status
        if status == AutomationStatus.attiva:
            a.activated_by = self.user_id
        self.repo.save(a, new_version=False)
        if self.s.scheduler is not None:
            self.s.scheduler.sync(a)
        if status == AutomationStatus.attiva:
            self.s.connector_check(a, self.user_id)  # a connector it sends through must be alive
        return a

    async def run(self, automation_id: str, trigger: RunTrigger = RunTrigger.prova) -> AutomationRun:
        a = self.get(automation_id)
        return await run_automation(self.s, a, trigger, {}, self.user_id)

    def undo(self, automation_id: str) -> Automation:
        a = self.get(automation_id)
        versions = self.repo.versions(automation_id)
        if len(versions) < 2:
            return a
        previous = versions[1]
        a.graph = previous.graph
        self.repo.save(a, change_note=f"Ripristinata la versione {previous.version}", author=self.user_id)
        if self.s.scheduler is not None:
            self.s.scheduler.sync(a)
        return a

    def apply_proposal(self, proposal_id: str, accept: bool) -> tuple[Proposal, Automation | None]:
        chats = ChatRepo(self.s.db, self.org_id)
        p = chats.proposal(proposal_id)
        if p is None or p.status != ProposalStatus.pending:
            raise KeyError(proposal_id)
        if not accept:
            p.status = ProposalStatus.rejected
            chats.save_proposal(p)
            return p, None
        a = self.get(p.automation_id)
        a.graph = apply_ops(a.graph, p.operations)
        self.repo.save(a, change_note=p.summary[:200], author=self.user_id)
        p.status = ProposalStatus.applied
        chats.save_proposal(p)
        if self.s.scheduler is not None:
            self.s.scheduler.sync(a)
        self.s.connector_check(a, self.user_id)
        return p, a


async def run_automation(services, a: Automation, trigger: RunTrigger, payload: dict, user_id: str) -> AutomationRun:  # type: ignore[no-untyped-def]
    ctx = services.agent_context(a.org_id, user_id)
    executor = AutomationExecutor(ctx, services.paths.org_outputs(a.org_id))
    run = await executor.run(a, trigger, payload)
    AutomationRepo(services.db, a.org_id).save_run(run)
    services.connector_check(a, user_id)  # a send that failed or was skipped: the connector needs attention
    return run
