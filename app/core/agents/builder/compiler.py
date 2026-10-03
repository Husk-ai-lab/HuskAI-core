"""FlowDraft (LLM output) → validated, laid-out AutomationGraph."""

from __future__ import annotations

from app.core.agents.builder.graph_ops import edge, layout
from app.core.agents.builder.schemas import FlowDraft
from app.data.models.automation import (
    ActionNode,
    ActionType,
    AgentNode,
    Attachment,
    AutomationGraph,
    ConditionNode,
    ConditionOp,
    DecisionNode,
    TriggerNode,
)
from app.helpers.text import slug


def _id(ref: str, used: set[str]) -> str:
    base = slug(ref, 30).replace("-", "_") or "nodo"
    out, i = base, 2
    while out in used:
        out = f"{base}_{i}"
        i += 1
    used.add(out)
    return out


def compile_draft(draft: FlowDraft) -> AutomationGraph:
    used: set[str] = {"trigger"}
    t = draft.trigger
    nodes: list = [
        TriggerNode(id="trigger", label=t.label, subtitle=t.subtitle, trigger=t.type, cron=t.cron,
                    folder=t.folder, pattern=t.pattern)
    ]
    ref_to_id = {"trigger": "trigger"}
    for s in draft.steps:
        ref_to_id[s.ref] = _id(s.ref, used)
    edges = []
    for s in draft.steps:
        nid = ref_to_id[s.ref]
        if s.kind == "agent":
            atts = [
                Attachment(id=f"{nid}_{i}", role=a.role, label=a.label, subtitle=a.subtitle,
                           document_ids=a.document_ids, tool=a.tool)
                for i, a in enumerate(s.attachments[:6])
            ]
            nodes.append(AgentNode(id=nid, label=s.label, subtitle=s.subtitle,
                                   instructions=s.instructions or s.label, attachments=atts, output_key=nid))
        elif s.kind == "decision":
            nodes.append(DecisionNode(id=nid, label=s.label, subtitle=s.subtitle, question=s.question or s.label,
                                      options=s.options or ["vero", "falso"]))
        elif s.kind == "condition":
            nodes.append(ConditionNode(id=nid, label=s.label, subtitle=s.subtitle, field=s.field or "risultato",
                                       op=s.op or ConditionOp.gt, value=s.value if s.value is not None else 0))
        else:
            nodes.append(ActionNode(id=nid, label=s.label, subtitle=s.subtitle, action=s.action or ActionType.log,
                                    target=s.target, template=s.template))
        src = ref_to_id.get(s.after, "trigger")
        edges.append(edge(src, nid, s.branch))
    return layout(AutomationGraph(nodes=nodes, edges=edges))
