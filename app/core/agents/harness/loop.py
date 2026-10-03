"""A small, explicit tool-calling loop over LangChain chat models.

Why not a prebuilt agent: we need checkpoints, hard iteration caps, tool errors fed back
to the model (so it can rewrite an invalid note), and events streamed to the UI.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.helpers.logging import get_logger

log = get_logger("agent.loop")

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
MAX_TOOL_RESULT_CHARS = 7000


@dataclass
class LoopResult:
    text: str
    messages: list[BaseMessage]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    stopped_by_cap: bool = False


async def run_tool_loop(
    model: BaseChatModel,
    messages: list[BaseMessage],
    tools: list[BaseTool],
    *,
    max_iterations: int = 10,
    on_event: EventSink | None = None,
    config: dict | None = None,
) -> LoopResult:
    by_name = {t.name: t for t in tools}
    bound = model.bind_tools(tools) if tools else model
    history = list(messages)
    calls: list[dict[str, Any]] = []
    for i in range(max_iterations):
        ai: AIMessage = await bound.ainvoke(history, config=config)  # type: ignore[assignment]
        history.append(ai)
        if not ai.tool_calls:
            return LoopResult(text=_text(ai), messages=history, tool_calls=calls, iterations=i + 1)
        for tc in ai.tool_calls:
            name, args = tc["name"], tc.get("args") or {}
            if on_event:
                await on_event("tool", {"name": name, "args": _short(args)})
            tool = by_name.get(name)
            if tool is None:
                content = f"ERROR: tool '{name}' does not exist. Tools: {', '.join(by_name)}"
            else:
                try:
                    out = await tool.ainvoke(args)
                    content = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
                except Exception as exc:  # fed back so the model can correct itself
                    content = f"ERROR: {exc}"
            if len(content) > MAX_TOOL_RESULT_CHARS:
                content = content[:MAX_TOOL_RESULT_CHARS] + " …[truncated]"
            calls.append({"name": name, "args": args, "ok": not content.startswith("ERROR")})
            history.append(ToolMessage(content=content, tool_call_id=tc["id"], name=name))
    # cap reached: ask for a final answer without tools
    final = await model.ainvoke(
        history + [_nudge()], config=config
    )
    history.append(final)
    return LoopResult(text=_text(final), messages=history, tool_calls=calls, iterations=max_iterations, stopped_by_cap=True)


def _nudge() -> BaseMessage:
    from langchain_core.messages import HumanMessage

    return HumanMessage(content="You reached the step limit. Give your final answer now, without tools.")


def _text(msg: AIMessage) -> str:
    c = msg.content
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c).strip()
    return str(c).strip()


def _short(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v) for k, v in args.items()}
