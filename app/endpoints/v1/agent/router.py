"""Assistente Husk (builder agent) chat, personalities, Husk Studio status."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.core.agents.builder.agent import BuilderAgent
from app.core.agents.harness.personalities import all_personalities, get_personality
from app.core.agents.harness.self_heal import with_self_heal
from app.core.automations.service import AutomationService
from app.core.services import Services
from app.core.suggestions.engine import SuggestionEngine
from app.data.models.agent import (
    Chat,
    ChatMessage,
    ChatRequest,
    MessageRole,
    Personality,
    Proposal,
)
from app.data.models.automation import Automation
from app.data.models.suggestion import SuggestionKind, SuggestionStatus
from app.data.models.system import StudioStatus
from app.data.models.user import PersonalityId
from app.endpoints.deps import CurrentUser, current_user, get_services, llm_ready, not_in_maintenance
from app.gateways.sqlite.repositories import AutomationRepo, ChatRepo, SuggestionRepo
from app.gateways.studio.tracing import exporting, span
from app.helpers.ids import new_id
from app.helpers.logging import get_logger
from app.i18n import t
from app.i18n.errors import api_error, request_lang

log = get_logger("chat")

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

@router.get("/personalities", response_model=list[Personality])
def personalities(lang: str = Depends(request_lang)) -> list[Personality]:
    return all_personalities(lang)


@router.get("/studio", response_model=StudioStatus)
async def studio(refresh: bool = False, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    if refresh:
        return await services.studio.probe()
    return services.studio.status


class ChatView(BaseModel):
    chat: Chat
    messages: list[ChatMessage]
    proposals: list[Proposal]
    suggestions: list[str]


def _suggestions(a: Automation, lang: str) -> list[str]:
    out = []
    for n in a.graph.nodes:
        if n.kind == "condition":
            out.append(t("agent.suggest_explain_false", lang, label=n.label))
            break
    if any(n.kind == "action" and n.action.value in ("email", "notify") for n in a.graph.nodes):
        out.append(t("agent.suggest_notify_purchasing", lang))
    trig = next((n for n in a.graph.nodes if n.kind == "trigger"), None)
    if trig is not None and trig.trigger.value == "schedule":
        out.append(t("agent.suggest_saturday", lang))
    out.append(t("agent.suggest_which_documents", lang))
    return out[:3]


@router.get("/chats/automation/{automation_id}", response_model=ChatView)
def chat_for_automation(automation_id: str, cu: CurrentUser = Depends(current_user),
                        services: Services = Depends(get_services)) -> ChatView:
    a = AutomationRepo(services.db, cu.org_id).get(automation_id)
    if not a:
        api_error(404, "errors.automation_not_found")
    chats = ChatRepo(services.db, cu.org_id)
    chat = chats.for_automation(automation_id, cu.id)
    if chat is None:
        chat = chats.create(Chat(id=new_id("chat"), org_id=cu.org_id, user_id=cu.id, automation_id=automation_id,
                                 title=a.title, personality=cu.settings.personality))
        chats.add_message(ChatMessage(id=new_id("msg"), chat_id=chat.id, role=MessageRole.assistant,
                                      content=t("agent.greeting", cu.settings.language.value)))
    return ChatView(chat=chat, messages=chats.messages(chat.id), proposals=chats.pending_proposals(chat.id),
                    suggestions=_suggestions(a, cu.settings.language.value))


class ChatPatch(BaseModel):
    personality: PersonalityId


@router.patch("/chats/{chat_id}", response_model=Chat)
def patch_chat(chat_id: str, body: ChatPatch, cu: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    chats = ChatRepo(services.db, cu.org_id)
    chat = chats.get(chat_id)
    if not chat:
        api_error(404, "errors.chat_not_found")
    chat.personality = body.personality
    chats.update(chat)
    return chat


@router.post("/chats/{chat_id}/messages", dependencies=[Depends(llm_ready), Depends(not_in_maintenance)])
async def send_message(chat_id: str, body: ChatRequest, cu: CurrentUser = Depends(current_user),
                       services: Services = Depends(get_services)) -> EventSourceResponse:
    chats = ChatRepo(services.db, cu.org_id)
    chat = chats.get(chat_id)
    if not chat or not chat.automation_id:
        api_error(404, "errors.chat_not_found")
    autos = AutomationRepo(services.db, cu.org_id)
    automation = autos.get(chat.automation_id)
    if not automation:
        api_error(404, "errors.automation_not_found")
    if body.personality and body.personality != chat.personality:
        chat.personality = body.personality
        chats.update(chat)
    suggestion = None
    if body.suggestion_id:
        suggestion = SuggestionRepo(services.db, cu.org_id).get(body.suggestion_id)
        if suggestion is None or suggestion.automation_id != automation.id:
            api_error(404, "errors.suggestion_not_found")
    # A suggestion message is the stored pre-made prompt, unchanged. Edited text is the user's own request.
    from_suggestion = suggestion is not None and body.content.strip() == (suggestion.prompt or "").strip()
    history = chats.messages(chat.id)
    meta: dict = {"selected_node_id": body.selected_node_id} if body.selected_node_id else {}
    if suggestion is not None:
        meta |= {"suggestion_id": suggestion.id, "document_ids": suggestion.document_ids}
        if from_suggestion:
            meta["origin"] = "suggestion"
    self_heal = body.self_heal if not from_suggestion else None
    if self_heal is not None:
        meta |= {"origin": "self_heal", "trace_id": self_heal.trace_id}
    user_msg = chats.add_message(ChatMessage(
        id=new_id("msg"), chat_id=chat.id, role=MessageRole.user, content=body.content, meta=meta,
    ))
    evidence = None
    if from_suggestion and suggestion is not None:
        names = {d.id: d.filename for d in services.knowledge(cu.org_id).docs.list()}
        evidence = "\n".join(
            f"- {names.get(e.document_id, e.document_id)} [{e.chunk_id or '-'}]: «{e.quote}»" for e in suggestion.evidence
        ) or "\n".join(f"- {names.get(d, d)}" for d in suggestion.document_ids)
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(kind: str, data: dict) -> None:
        await queue.put({"event": kind, "data": data})

    async def work() -> None:
        ctx = services.agent_context(cu.org_id, cu.id)
        agent = BuilderAgent(ctx)
        personality = get_personality(chat.personality, ctx.language)
        trace: dict = {}
        try:
            await emit("status", {"key": "chat.status_working"})
            with span("husk.builder.chat", **{"husk.automation_id": automation.id}) as trace_id:
                trace.update(id=trace_id, traced=exporting())

                async def attempt(hint: str | None):  # type: ignore[no-untyped-def]
                    if hint:
                        await emit("status", {"key": "chat.status_self_heal"})
                    return await agent.chat(automation, history, body.content, chat.personality,
                                            body.selected_node_id, emit, suggestion_evidence=evidence,
                                            self_heal_trace=self_heal.trace_id if self_heal else None, studio_hint=hint)

                # a crashed request is retried once with Husk Studio's diagnosis (only when its MCP is up)
                outcome = await with_self_heal(attempt, services.studio, lambda: trace.get("id"))
            meta: dict = {"personality": personality.id.value}
            if from_suggestion:
                meta["reply_to_suggestion"] = True
            if self_heal is not None:
                meta["reply_to_self_heal"] = True
            if outcome.operations:
                if outcome.proposal_required:
                    prop = Proposal(id=new_id("prop"), org_id=cu.org_id, chat_id=chat.id, automation_id=automation.id,
                                    summary=outcome.reply[:500], operations=outcome.operations,
                                    suggestion_id=suggestion.id if from_suggestion and suggestion else None,
                                    origin="suggestion" if from_suggestion else "self_heal" if self_heal else None)
                    chats.save_proposal(prop)
                    meta["proposal_id"] = prop.id
                    await emit("proposal", prop.model_dump(mode="json"))
                else:
                    automation.graph = outcome.graph  # type: ignore[assignment]
                    saved = autos.save(automation, change_note=body.content[:200], author="builder")
                    if services.scheduler:
                        services.scheduler.sync(saved)
                    services.connector_check(saved, cu.id)
                    meta["version"] = saved.version
                    await emit("patch", {"automation": saved.model_dump(mode="json")})
            msg = chats.add_message(ChatMessage(id=new_id("msg"), chat_id=chat.id, role=MessageRole.assistant,
                                                content=outcome.reply, meta=meta))
            await emit("message", msg.model_dump(mode="json"))
        except Exception as exc:
            log.warning("builder chat failed: %s", type(exc).__name__)
            text = t("chat.error_reply", ctx.language, detail=str(exc)[:200])
            # the trace id, not a link: the UI decides "Show in Husk Studio" vs "Install Husk Studio"
            msg = chats.add_message(ChatMessage(id=new_id("msg"), chat_id=chat.id, role=MessageRole.assistant, content=text,
                                                meta={"error": True, "trace_id": trace.get("id"), "traced": trace.get("traced", False)}))
            await emit("error", msg.model_dump(mode="json"))
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())

    async def gen():  # type: ignore[no-untyped-def]
        yield {"event": "user", "data": json.dumps(user_msg.model_dump(mode="json"), ensure_ascii=False)}
        while True:
            item = await queue.get()
            if item is None:
                break
            yield {"event": item["event"], "data": json.dumps(item["data"], ensure_ascii=False, default=str)}
        await task
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(gen())


class ProposalDecision(BaseModel):
    accept: bool


@router.post("/proposals/{proposal_id}")
async def decide_proposal(proposal_id: str, body: ProposalDecision, cu: CurrentUser = Depends(current_user),
                    services: Services = Depends(get_services)) -> dict:
    try:
        p, a = AutomationService(services, cu.org_id, cu.id).apply_proposal(proposal_id, body.accept)
    except KeyError:
        api_error(404, "errors.proposal_not_found")
    except Exception as exc:
        api_error(400, "errors.proposal_not_applicable", detail=str(exc)[:200])
    if p.suggestion_id:
        _close_suggestion(services, cu, p.suggestion_id, body.accept)
    return {"proposal": p.model_dump(mode="json"), "automation": a.model_dump(mode="json") if a else None}


def _close_suggestion(services: Services, cu: CurrentUser, suggestion_id: str, accepted: bool) -> None:
    """Accepted → applied, and the new document joins the automation's notes (background). Discarded → dismissed."""
    repo = SuggestionRepo(services.db, cu.org_id)
    s = repo.set_status(suggestion_id, SuggestionStatus.applied if accepted else SuggestionStatus.dismissed)
    if s and accepted and s.kind == SuggestionKind.modify_automation:
        services.spawn(f"suggest-refresh:{s.id}", SuggestionEngine(services, cu.org_id, cu.id).refresh_network(s))
