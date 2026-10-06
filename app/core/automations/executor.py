"""Runs an automation graph locally: trigger → nodes in order, following branches."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.llm import human, structured, system
from app.core.automations.actions import action_body, render_template, run_action
from app.core.automations.decision import DecisionProvider, LLMDecisionProvider
from app.core.knowledge.ingest import convert, kind_for
from app.data.models.automation import (
    ActionNode,
    ActionType,
    AgentNode,
    Automation,
    AutomationRun,
    AutomationRunStatus,
    ConditionNode,
    ConditionOp,
    NodeRunLog,
    RunTrigger,
)
from app.data.models.connector import ConnectorKind
from app.gateways.studio.tracing import exporting, span
from app.helpers.ids import new_id
from app.helpers.logging import get_logger
from app.helpers.text import truncate
from app.helpers.time import utcnow
from app.i18n import t

log = get_logger("executor")
MAX_STEPS = 60


class AgentNodeOutput(BaseModel):
    text: str = Field(..., description="result readable by a person, in the user's language")
    values: dict[str, float | int | str | bool] = Field(
        default_factory=dict, description="key values extracted, e.g. {'anomalies': 3, 'oee': 71.5}"
    )


def lookup(state: dict[str, Any], field: str) -> Any:
    if field in state:
        return state[field]
    cur: Any = state
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def evaluate(node: ConditionNode, state: dict[str, Any]) -> bool:
    left = lookup(state, node.field)
    right = node.value
    if node.op == ConditionOp.contains:
        return str(right).lower() in str(left or "").lower()
    try:
        lf, rf = float(left), float(right)  # type: ignore[arg-type]
        left, right = lf, rf
    except (TypeError, ValueError):
        left, right = str(left), str(right)
    return {
        ConditionOp.gt: lambda: left > right,
        ConditionOp.gte: lambda: left >= right,
        ConditionOp.lt: lambda: left < right,
        ConditionOp.lte: lambda: left <= right,
        ConditionOp.eq: lambda: left == right,
        ConditionOp.ne: lambda: left != right,
    }[node.op]()


class AutomationExecutor:
    def __init__(self, ctx: AgentContext, out_root: Path, decisions: DecisionProvider | None = None):
        self.ctx = ctx
        self.out_root = out_root
        self.decisions = decisions or LLMDecisionProvider(ctx)

    async def run(self, automation: Automation, trigger: RunTrigger, payload: dict[str, Any] | None = None) -> AutomationRun:
        test_mode = trigger == RunTrigger.prova
        run = AutomationRun(id=new_id("arun"), org_id=automation.org_id, automation_id=automation.id, trigger=trigger)
        out_dir = (self.out_root / "prova" / run.id) if test_mode else self.out_root / automation.id
        state: dict[str, Any] = {"trigger": payload or {}, "_stamp": utcnow().strftime("%Y%m%d-%H%M%S")}
        with span("husk.automation.run", **{"husk.automation_id": automation.id, "husk.trigger": trigger.value}) as tid:
            run.trace_id, run.traced = tid, exporting()
            try:
                await self._walk(automation, state, run, out_dir, test_mode)
                run.status = AutomationRunStatus.success
            except Exception as exc:
                log.warning("automation failed id=%s err=%s", automation.id, type(exc).__name__)
                run.status, run.error = AutomationRunStatus.failed, str(exc)[:400]
        run.ended_at = utcnow()
        return run

    async def _walk(self, a: Automation, state: dict[str, Any], run: AutomationRun, out_dir: Path, test_mode: bool) -> None:
        g = a.graph
        trig = g.trigger()
        run.log.append(NodeRunLog(node_id=trig.id, label=trig.label, status="ok", output=state["trigger"] or None))
        if state["trigger"].get("path"):
            state["file"] = self._read_payload_file(Path(state["trigger"]["path"]))
        frontier = g.successors(trig.id)
        steps = 0
        visited: set[str] = set()
        while frontier:
            steps += 1
            if steps > MAX_STEPS:
                raise RuntimeError("too many steps")
            nid = frontier.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            node = g.node(nid)
            t0 = time.perf_counter()
            branch: str | None = None
            try:
                if node.kind == "agent":
                    out = await self._agent(node, state, a)
                    state[node.output_key] = out.text
                    state.update(out.values)
                    state["_summary"] = out.text
                    entry = NodeRunLog(node_id=nid, label=node.label, status="ok", output=out.model_dump())
                elif node.kind == "decision":
                    d = await self.decisions.decide(self._state_text(state), node.question, node.options)
                    branch = d.answer
                    state[nid] = d.answer
                    entry = NodeRunLog(node_id=nid, label=node.label, status="ok",
                                       output={"answer": d.answer, "probability": round(d.probability, 2)},
                                       message=d.reason or None)
                elif node.kind == "condition":
                    ok = evaluate(node, state)
                    branch = "vero" if ok else "falso"
                    entry = NodeRunLog(node_id=nid, label=node.label, status="ok",
                                       output={"field": node.field, "value": lookup(state, node.field), "outcome": branch})
                elif node.kind == "action" and node.action.connector:
                    status, output, message = await self._send(node, state, a, test_mode)
                    entry = NodeRunLog(node_id=nid, label=node.label, status=status, output=output, message=message)
                elif node.kind == "action":
                    status, output, message = run_action(node, state, out_dir, a.title, test_mode, self.ctx.language)
                    if node.action == ActionType.pdf_report and status == "ok" and isinstance(output, str):
                        state["_report"] = output  # a later send step can attach it
                    entry = NodeRunLog(node_id=nid, label=node.label, status=status, output=output, message=message)
                else:
                    entry = NodeRunLog(node_id=nid, label=node.label, status="skipped")
            except Exception as exc:
                entry = NodeRunLog(node_id=nid, label=node.label, status="error", message=str(exc)[:300],
                                   duration_ms=int((time.perf_counter() - t0) * 1000))
                run.log.append(entry)
                raise RuntimeError(f"«{node.label}»: {exc}") from exc
            entry.duration_ms = int((time.perf_counter() - t0) * 1000)
            run.log.append(entry)
            frontier += g.successors(nid, branch) if node.kind in ("decision", "condition") else g.successors(nid)

    async def _send(self, node: ActionNode, state: dict[str, Any], a: Automation, test_mode: bool) -> tuple[str, Any, str]:
        """Telegram / email through the connector. Not set up → the step is skipped (the run goes on, a suggestion
        asks the user to set the connector up); the send itself failing fails the run. The log keeps no body."""
        lang = self.ctx.language
        kind = ConnectorKind(node.action.connector)
        if self.ctx.connectors is None:
            return "skipped", {"connector": kind.value, "reason": "not_configured"}, t(
                "run.connector_not_ready", lang, connector=t(f"connectors.name_{kind.value}", lang))
        report = state.get("_report")
        attachment = Path(report) if node.attach_report and report and Path(report).is_file() else None
        res = await self.ctx.connectors.send(
            self.ctx.org_id, kind, subject=render_template(node.subject, state, node.label or a.title),
            body=action_body(node, state), target=node.target, attachment=attachment, test_mode=test_mode, lang=lang)
        message = t(res.message_key, lang, **res.params)
        if res.status == "error":
            raise RuntimeError(message)
        return res.status, res.output, message

    def _state_text(self, state: dict[str, Any]) -> str:
        return "\n".join(f"{k}: {truncate(str(v), 1500)}" for k, v in state.items() if not k.startswith("_"))

    def _read_payload_file(self, path: Path) -> str:
        try:
            kind = kind_for(path.name)
            conv = convert(path, kind, self.ctx.org_id, "trigger")
            return truncate(conv.markdown, 6000)
        except Exception as exc:
            return f"(unreadable file: {type(exc).__name__})"

    async def _agent(self, node: AgentNode, state: dict[str, Any], a: Automation) -> AgentNodeOutput:
        ks = self.ctx.knowledge
        context_parts = []
        for att in node.attachments:
            if att.role.value == "memoria" and att.document_ids:
                hits = ks.search_chunks(node.instructions, k=4, document_ids=att.document_ids)
                context_parts += [f"[{att.label}] {truncate(h.text, 900)}" for h in hits]
            elif att.role.value == "strumento" and att.document_ids:
                tools = ks.tabular()
                for did in att.document_ids[:3]:
                    doc = ks.docs.get(did)
                    if doc and doc.kind.is_tabular:
                        try:
                            context_parts.append(f"[{att.label}] {tools.describe_table(did)}")
                            context_parts.append(f"[{att.label}] {tools.missing_values(did)}")
                        except Exception as exc:
                            context_parts.append(f"[{att.label}] read error: {exc}")
                    elif doc:
                        context_parts.append(f"[{att.label}] {truncate(ks.docs.markdown(did), 1500)}")
        msgs = [
            system(f"You are an AI agent inside the automation «{a.title}». Do the task using only the data given. "
                   "Put the numbers and outcomes useful to the next steps in 'values'. " + self.ctx.write_in()),
            human(f"TASK: {node.instructions}\n\nSTATE:\n{self._state_text(state)}\n\nDATA:\n" +
                  ("\n\n".join(context_parts) or "(no linked data)")),
        ]
        return await structured(self.ctx.model(role="runtime"), AgentNodeOutput, msgs, config={"run_name": f"automation.agent.{node.id}"})
