"""Husk Studio's MCP tools as Builder tools (only when a local Studio with MCP is running).

No code coupling (rule 2): the tools are whatever this Studio advertises through MCP, filtered
to read-only names (`get_*`, `list_*`, `explain_*`, `search_*`) by the client. They are bound
only when useful (a failure to diagnose), so normal chat turns don't pay for their schemas.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.gateways.studio.client import StudioClient

# Words that suggest the user is asking about a failure (en + it).
_FAILURE_WORDS = re.compile(
    r"\b(error|errors|fail\w*|crash\w*|broke\w*|why|debug\w*|trace|studio|"
    r"errore|errori|fallit\w*|fallisc\w*|non funziona|perch[eé]|bloccat\w*|guast\w*)\b",
    re.IGNORECASE,
)


def mentions_failure(text: str) -> bool:
    return bool(_FAILURE_WORDS.search(text or ""))


def _schema(raw: dict[str, Any]) -> dict[str, Any]:
    schema = dict(raw or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def studio_tools(studio: StudioClient | None) -> list[BaseTool]:
    """One LangChain tool per read-only Studio MCP tool; [] when Studio or its MCP is absent."""
    if studio is None or not studio.status.mcp:
        return []
    tools: list[BaseTool] = []
    for d in studio.tool_defs:
        name = d["name"]

        async def call(_name: str = name, **kwargs: Any) -> str:
            text = await studio.call_tool(_name, kwargs)
            return text if text else "Husk Studio returned nothing for this request."

        tools.append(StructuredTool(
            name=f"studio_{name}",
            description=f"[Husk Studio, read-only] {d.get('description') or name}"[:900],
            args_schema=_schema(d.get("input_schema") or {}),
            coroutine=call,
        ))
    return tools
