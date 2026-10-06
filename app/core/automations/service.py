"""Automation use cases: status changes (Carica / Attiva), Prova, history, undo, proposals."""

from __future__ import annotations

from app.core.agents.builder.graph_ops import GraphOpError, apply_op, apply_ops
from app.core.automations.executor import AutomationExecutor
from app.data.models.agent import Proposal, ProposalStatus
from app.data.models.automation import (
    Automation,
    AutomationRun,
    AutomationStatus,
    NotificationNode,
    RunTrigger,
)
from app.gateways.connectors.gmail import normalise_recipients
from app.gateways.sqlite.repositories import AutomationRepo, ChatRepo, UserRepo
from app.i18n import t
from app.i18n.errors import LocalizedError


class AutomationNotFound(KeyError):
    pass


NOTIFICATION_FIELDS = ("channel", "recipients", "subject", "template", "attach_report")


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

    def edit_notification(self, automation_id: str, node_id: str, changes: dict) -> Automation:
        """The one thing a user sets by hand: how a notification node delivers (channel, recipients, subject, message,
        report). Same road as the Builder: one `update_node` through graph_ops, validated by AutomationGraph, saved as
        a new version (so Undo works), scheduler and connector check follow. It is the user's own decision, so it is
        applied at once, never as a proposal."""
        a = self.get(automation_id)
        try:
            node = a.graph.node(node_id)
        except KeyError:
            raise LocalizedError("errors.node_not_found", status=404) from None
        if not isinstance(node, NotificationNode):
            raise LocalizedError("errors.node_not_notification", status=400)
        changes = {k: v for k, v in changes.items() if k in NOTIFICATION_FIELDS}
        if "recipients" in changes:
            try:
                changes["recipients"] = normalise_recipients(changes["recipients"])
            except ValueError:
                raise LocalizedError("errors.notification_recipients_invalid", status=400) from None
        try:
            a.graph = apply_op(a.graph, {"op": "update_node", "node_id": node_id, "changes": changes})
        except GraphOpError:
            raise LocalizedError("errors.node_edit_invalid", status=400) from None
        lang = UserRepo(self.s.db).get_settings(self.user_id).language.value
        self.repo.save(a, change_note=t("automations.change_note_notification", lang, node=node.label)[:200], author=self.user_id)
        if self.s.scheduler is not None:
            self.s.scheduler.sync(a)
        self.s.connector_check(a, self.user_id)
        return a

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
