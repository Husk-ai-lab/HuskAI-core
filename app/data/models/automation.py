"""Automation graphs.

An automation is a small directed acyclic graph: one trigger, then AI agent nodes,
decision nodes, conditions, actions and notifications. The builder agent changes it through typed patch tools
(`graph_ops`); the only thing a user sets by hand is the delivery of a notification node (channel, recipients,
message). Every change is validated here and stored as a new version.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.data.models.common import HuskModel, OrgScoped
from app.helpers.time import utcnow


class Position(HuskModel):
    x: float = 0
    y: float = 0


class _NodeBase(HuskModel):
    id: str = Field(..., pattern=r"^[a-z0-9_\-]{1,48}$")
    label: str = Field(..., min_length=1, max_length=80)
    subtitle: str | None = Field(None, max_length=80)  # e.g. "on file · cartella condivisa"
    position: Position = Field(default_factory=Position)


class TriggerType(StrEnum):
    schedule = "schedule"
    file = "file"
    manual = "manual"


class TriggerNode(_NodeBase):
    kind: Literal["trigger"] = "trigger"
    trigger: TriggerType = TriggerType.manual
    cron: str | None = None  # "0 18 * * *"
    folder: str | None = None  # watched folder for file triggers
    pattern: str | None = None  # "*.xlsx"


class AttachmentRole(StrEnum):
    model = "modello"
    memory = "memoria"
    tool = "strumento"


class Attachment(HuskModel):
    id: str = Field(..., pattern=r"^[a-z0-9_\-]{1,48}$")
    role: AttachmentRole
    label: str = Field(..., max_length=60)
    subtitle: str | None = Field(None, max_length=60)  # "get: articoli"
    document_ids: list[str] = Field(default_factory=list)  # memory/tool grounded on documents
    tool: str | None = None  # tabular tool name or simulated connector


class AgentNode(_NodeBase):
    kind: Literal["agent"] = "agent"
    instructions: str = Field(..., max_length=4000)
    attachments: list[Attachment] = Field(default_factory=list)
    output_key: str = "result"


class DecisionNode(_NodeBase):
    """Typed decision (yes/no or pick one). Served by the LLM now, Serenity later."""

    kind: Literal["decision"] = "decision"
    question: str = Field(..., max_length=400)
    options: list[str] = Field(default_factory=lambda: ["vero", "falso"], min_length=2, max_length=10)
    threshold: float = Field(0.0, ge=0, le=1)  # below → ask a person (future)


class ConditionOp(StrEnum):
    gt = ">"
    gte = ">="
    lt = "<"
    lte = "<="
    eq = "=="
    ne = "!="
    contains = "contains"


class ConditionNode(_NodeBase):
    kind: Literal["condition"] = "condition"
    field: str = Field(..., max_length=80)  # state key, e.g. "anomalie" or "analisi.oee"
    op: ConditionOp = ConditionOp.gt
    value: float | str = 0


class ActionType(StrEnum):
    write_file = "write_file"
    write_xlsx = "write_xlsx"
    pdf_report = "pdf_report"
    log = "log"
    erp_update = "erp_update"  # simulated in this phase

    @property
    def simulated(self) -> bool:
        return self == ActionType.erp_update


class ActionNode(_NodeBase):
    """Work done on this computer: files, PDF, Excel, a log line. Anything that SENDS is a NotificationNode."""

    kind: Literal["action"] = "action"
    action: ActionType = ActionType.log
    target: str | None = Field(None, max_length=200)  # folder or ERP entity
    template: str | None = Field(None, max_length=4000)  # report/file body with {placeholders}


class NotificationChannel(StrEnum):
    desktop = "desktop"  # a notification on this computer, no connector
    telegram = "telegram"  # the Telegram connector
    gmail = "gmail"  # the Gmail connector

    @property
    def connector(self) -> str | None:
        """The connector this channel sends through, if any (desktop has none)."""
        return None if self == NotificationChannel.desktop else self.value


class NotificationNode(_NodeBase):
    """Everything that tells someone something: a desktop notification, a Telegram message, an email, with or
    without the PDF report. One channel per node; several channels are several nodes. The user (or the assistant)
    chooses the channel and, for email, the recipients."""

    kind: Literal["notification"] = "notification"
    channel: NotificationChannel = NotificationChannel.desktop
    recipients: str | None = Field(None, max_length=400)  # Gmail: address(es); Telegram sends to its configured chat
    subject: str | None = Field(None, max_length=200)  # email subject, with {placeholders}
    template: str | None = Field(None, max_length=4000)  # the message, with {placeholders}; empty = run summary
    attach_report: bool = False  # attach the PDF made by an earlier pdf_report step (Telegram, Gmail)


Node = Annotated[
    TriggerNode | AgentNode | DecisionNode | ConditionNode | ActionNode | NotificationNode,
    Field(discriminator="kind"),
]


class Edge(HuskModel):
    id: str
    source: str
    target: str
    branch: str | None = None  # "vero"/"falso" or a decision option


class AutomationGraph(HuskModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> AutomationGraph:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        triggers = [n for n in self.nodes if n.kind == "trigger"]
        if self.nodes and len(triggers) != 1:
            raise ValueError("an automation needs exactly one trigger")
        known = set(ids)
        for e in self.edges:
            if e.source not in known or e.target not in known:
                raise ValueError(f"edge {e.id} references an unknown node")
            if e.target in {t.id for t in triggers}:
                raise ValueError("nothing can point into the trigger")
        by_id = {n.id: n for n in self.nodes}
        for n in self.nodes:
            out = [e for e in self.edges if e.source == n.id]
            if n.kind == "condition":
                bad = [e for e in out if e.branch not in ("vero", "falso")]
                if bad:
                    raise ValueError(f"condition {n.id}: outgoing edges must be 'vero' or 'falso'")
            if n.kind == "decision":
                bad = [e for e in out if e.branch not in n.options]  # type: ignore[union-attr]
                if bad:
                    raise ValueError(f"decision {n.id}: outgoing edges must use one of its options")
        if _has_cycle(ids, self.edges):
            raise ValueError("the flow must not contain loops")
        _ = by_id
        return self

    def node(self, node_id: str) -> Any:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def trigger(self) -> TriggerNode:
        return next(n for n in self.nodes if n.kind == "trigger")  # type: ignore[return-value]

    def successors(self, node_id: str, branch: str | None = None) -> list[str]:
        return [
            e.target
            for e in self.edges
            if e.source == node_id and (branch is None or e.branch in (None, branch))
        ]


def _has_cycle(ids: list[str], edges: list[Edge]) -> bool:
    graph: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        graph[e.source].append(e.target)
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        state[n] = 1
        for m in graph[n]:
            if state.get(m) == 1 or (state.get(m) is None and visit(m)):
                return True
        state[n] = 2
        return False

    return any(state.get(i) is None and visit(i) for i in ids)


class AutomationStatus(StrEnum):
    bozza = "bozza"
    caricata = "caricata"  # "Carica": saved and ready, not scheduled
    attiva = "attiva"  # "Attiva": scheduled locally
    errore = "errore"


class Automation(OrgScoped):
    id: str
    title: str = Field(..., max_length=120)
    description: str = Field("", max_length=1000)
    status: AutomationStatus = AutomationStatus.bozza
    graph: AutomationGraph = Field(default_factory=AutomationGraph)
    source_document_ids: list[str] = Field(default_factory=list)
    hours_per_week: float = 0
    priority: str = "media"
    network_id: str | None = None
    run_id: str | None = None
    activated_by: str | None = None  # whose usage local scheduled runs are attributed to
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AutomationVersion(HuskModel):
    automation_id: str
    version: int
    graph: AutomationGraph
    change_note: str = ""
    author: str  # "builder" or user id
    created_at: datetime = Field(default_factory=utcnow)


class RunTrigger(StrEnum):
    prova = "prova"
    schedule = "schedule"
    file = "file"
    manual = "manual"


class AutomationRunStatus(StrEnum):
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class NodeRunLog(HuskModel):
    node_id: str
    label: str
    status: Literal["ok", "error", "skipped", "simulated"]
    output: Any = None
    message: str | None = None
    duration_ms: int = 0


class AutomationRun(OrgScoped):
    id: str
    automation_id: str
    trigger: RunTrigger
    status: AutomationRunStatus = AutomationRunStatus.running
    log: list[NodeRunLog] = Field(default_factory=list)
    error: str | None = None
    trace_id: str | None = None
    traced: bool = False  # the trace was exported to a running Husk Studio
    tokens: int = 0
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
