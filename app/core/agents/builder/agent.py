"""Builder: drafts automations from discovery networks and edits them through chat
(the plan's "Automator" role, powering "Assistente Husk")."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.core.agents.builder import prompts
from app.core.agents.builder.compiler import compile_draft
from app.core.agents.builder.graph_ops import GraphOpError, apply_op, compact
from app.core.agents.builder.Rag.context import builder_context, needs_notes
from app.core.agents.builder.schemas import FlowDraft
from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.llm import human, structured, system
from app.core.agents.harness.loop import run_tool_loop
from app.core.agents.harness.personalities import get_personality
from app.core.agents.harness.tools import data_tools, knowledge_tools, memory_read_tools
from app.data.models.agent import ChatMessage, MessageRole, Personality
from app.data.models.automation import Automation, AutomationGraph
from app.data.models.discovery import Opportunity
from app.data.models.knowledge import Network
from app.helpers.logging import get_logger
from app.helpers.text import truncate
from app.i18n import t

log = get_logger("builder")
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


class AddNodeArgs(BaseModel):
    node: dict[str, Any] = Field(..., description="full node without id/position, e.g. {'kind':'action','label':'Notify Purchasing','action':'email','target':'Purchasing'}")
    after: str | None = Field(None, description="id of the node to connect it after")
    branch: str | None = Field(None, description="branch of the edge from 'after' (vero/falso or a decision option)")
    before: str | None = Field(None, description="to insert it between 'after' and this node")


class UpdateNodeArgs(BaseModel):
    node_id: str
    changes: dict[str, Any] = Field(..., description="fields to change, e.g. {'value': 80} or {'instructions': '...'}")


class NodeIdArgs(BaseModel):
    node_id: str


class ConnectArgs(BaseModel):
    source: str
    target: str
    branch: str | None = None


class DisconnectArgs(BaseModel):
    source: str
    target: str


class TriggerArgs(BaseModel):
    trigger: str | None = Field(None, description="schedule, file or manual")
    cron: str | None = None
    folder: str | None = None
    pattern: str | None = None
    label: str | None = None
    subtitle: str | None = None


class NoArgs(BaseModel):
    pass


class ChatOutcome(BaseModel):
    reply: str
    operations: list[dict[str, Any]] = Field(default_factory=list)
    graph: AutomationGraph | None = None
    proposal_required: bool = False


class BuilderAgent:
    name = "builder"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    # --- drafting after discovery --------------------------------------------------------------
    async def draft(self, opp: Opportunity, network: Network, hint: str | None = None) -> tuple[FlowDraft, AutomationGraph]:
        ctx = self.ctx
        notes = ctx.memory.networks.render(network, ctx.language)
        docs = {d.id: d.filename for d in ctx.knowledge.docs.list() if d.id in network.document_ids}
        msgs: list[BaseMessage] = [
            system(prompts.SYSTEM.format(write_in=ctx.write_in(), personality="")
                   + (f"\nHint from the debugger: {hint}" if hint else "")),
            human(prompts.DRAFT.format(
                title=opp.title, explanation=opp.explanation, hours=opp.hours_per_week,
                profile=ctx.memory.profile.markdown(ctx.language), notes=truncate(notes, 9000),
                documents=json.dumps(docs, ensure_ascii=False),
            )),
        ]
        last: Exception | None = None
        for _ in range(2):
            draft = await structured(ctx.model(), FlowDraft, msgs, config={"run_name": "builder.draft"})
            try:
                return draft, compile_draft(draft)
            except Exception as exc:  # invalid graph: tell the model exactly why, once
                last = exc
                msgs = msgs + [AIMessage(content=draft.model_dump_json()),
                               HumanMessage(content=f"The flow is not valid: {exc}. Fix it.")]
        raise GraphOpError(f"bozza non valida: {last}")

    async def summarize(self, title: str, network: Network) -> str:
        notes = self.ctx.memory.networks.render(network, self.ctx.language)
        msg = await self.ctx.model().ainvoke(
            [system("Write precise, short markdown summaries. " + self.ctx.write_in()),
             human(prompts.SUMMARY.format(title=title, notes=truncate(notes, 9000)))],
            config={"run_name": "builder.summary"},
        )
        body = str(msg.content).strip()
        return f"# {t('agent.summary_heading', self.ctx.language, title=title)}\n\n{body}\n"

    # --- chat editing ----------------------------------------------------------------------------
    def _graph_tools(self, state: dict[str, Any], personality: Personality) -> list[BaseTool]:
        def record(op: dict[str, Any]) -> str:
            try:
                state["graph"] = apply_op(state["graph"], op)  # validate on the working copy
            except GraphOpError as exc:
                raise ValueError(str(exc)) from exc
            state["ops"].append(op)
            verb = "proposed (waiting for confirmation)" if personality.requires_confirmation else "applied"
            return f"change {verb}. Flow now:\n{compact(state['graph'])}"

        def get_flow() -> str:
            return compact(state["graph"])

        def add_node(node: dict, after: str | None = None, branch: str | None = None, before: str | None = None) -> str:
            return record({"op": "add_node", "node": node, "after": after, "branch": branch, "before": before})

        def update_node(node_id: str, changes: dict) -> str:
            return record({"op": "update_node", "node_id": node_id, "changes": changes})

        def remove_node(node_id: str) -> str:
            return record({"op": "remove_node", "node_id": node_id})

        def connect(source: str, target: str, branch: str | None = None) -> str:
            return record({"op": "connect", "source": source, "target": target, "branch": branch})

        def disconnect(source: str, target: str) -> str:
            return record({"op": "disconnect", "source": source, "target": target})

        def set_trigger(**kw: Any) -> str:
            return record({"op": "set_trigger", **{k: v for k, v in kw.items() if v is not None}})

        def read_automation_notes() -> str:
            net = self.ctx.memory.repo.network_for_automation(state["automation_id"])
            return self.ctx.memory.networks.render(net, self.ctx.language) if net else "no linked network"

        T = StructuredTool.from_function
        return [
            T(get_flow, name="get_flow", args_schema=NoArgs, description="Show the current flow."),
            T(add_node, name="add_node", args_schema=AddNodeArgs, description="Add a node to the flow."),
            T(update_node, name="update_node", args_schema=UpdateNodeArgs, description="Change fields of a node."),
            T(remove_node, name="remove_node", args_schema=NodeIdArgs, description="Remove a node (reconnects the flow)."),
            T(connect, name="connect", args_schema=ConnectArgs, description="Connect two nodes."),
            T(disconnect, name="disconnect", args_schema=DisconnectArgs, description="Disconnect two nodes."),
            T(set_trigger, name="set_trigger", args_schema=TriggerArgs, description="Change when the automation starts."),
            T(read_automation_notes, name="read_automation_notes", args_schema=NoArgs,
              description="Knowledge-network notes of this automation (linked documents and activities)."),
        ]

    async def chat(
        self,
        automation: Automation,
        history: list[ChatMessage],
        user_text: str,
        personality_id: str | None,
        selected_node_id: str | None,
        emit: Emit | None = None,
        suggestion_evidence: str | None = None,
    ) -> ChatOutcome:
        """`suggestion_evidence` set → the request comes from a Husk suggestion: extra rules, tagged message,
        and the result is ALWAYS a proposal, whatever the personality."""
        ctx = self.ctx
        personality = get_personality(personality_id, ctx.language)
        state: dict[str, Any] = {"graph": automation.graph.model_copy(deep=True), "ops": [], "automation_id": automation.id}
        selected = "none"
        if selected_node_id:
            try:
                selected = str(automation.graph.node(selected_node_id).model_dump(exclude={"position"}, exclude_none=True))
            except KeyError:
                pass
        memory = builder_context(ctx, automation.id, user_text)
        if needs_notes(user_text) and emit:
            await emit("status", {"key": "chat.status_reading_documents"})
        msgs: list[BaseMessage] = [
            system(prompts.SYSTEM.format(write_in=ctx.write_in(), personality=personality.prompt)),
            system(prompts.CHAT_CONTEXT.format(
                title=automation.title, automation_id=automation.id, description=automation.description,
                flow=compact(automation.graph), selected=selected,
                notes_flag=", network notes" if needs_notes(user_text) else "", memory=truncate(memory, 9000),
            )),
        ]
        for m in history[-12:]:
            if m.role == MessageRole.user:
                msgs.append(HumanMessage(content=_tagged(m.content) if m.meta.get("origin") == "suggestion" else m.content))
            elif m.role == MessageRole.assistant:
                msgs.append(AIMessage(content=m.content))
        is_suggestion = suggestion_evidence is not None
        if is_suggestion:
            msgs.append(system(prompts.SUGGESTION_RULES.format(evidence=suggestion_evidence or "(none)")))
        msgs.append(HumanMessage(content=_tagged(user_text) if is_suggestion else user_text))
        tools = self._graph_tools(state, personality) + knowledge_tools(ctx) + data_tools(ctx) + memory_read_tools(ctx)

        async def on_event(kind: str, data: dict[str, Any]) -> None:
            if emit:
                await emit(kind, data)

        result = await run_tool_loop(
            ctx.model(), msgs, tools, max_iterations=ctx.settings.agent_max_iterations, on_event=on_event,
            config={"run_name": "builder.chat", "metadata": {"automation_id": automation.id}},
        )
        reply = result.text or t("agent.reply_changes_ready" if state["ops"] else "agent.reply_done", ctx.language)
        return ChatOutcome(
            reply=reply,
            operations=state["ops"],
            graph=state["graph"] if state["ops"] else None,
            proposal_required=(personality.requires_confirmation or is_suggestion) and bool(state["ops"]),
        )


def _tagged(text: str) -> str:
    return f"{prompts.SUGGESTION_TAG}\n{text}"
