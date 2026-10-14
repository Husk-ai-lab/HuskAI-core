"""Husk Studio compatibility — detection, tracing and MCP self-heal.

Zero code coupling (architecture rule 1): nothing from husk-studio is imported. Husk only
speaks the two public contracts:
  * OTLP/HTTP traces to Studio's loopback ingest (`/v1/traces`), GenAI/OpenInference spans
  * MCP tools, discovered with list_tools() and used only if present

Remove Studio and every method here degrades to a no-op.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.data.models.system import StudioStatus
from app.helpers.logging import get_logger
from app.helpers.settings import Settings
from app.helpers.time import utcnow

log = get_logger("studio")

PROBE_TIMEOUT_S = 2.0  # a busy Studio (ingesting spans) can take longer than a second to answer
MISSES_BEFORE_DOWN = 2  # one missed probe does not flip a running Studio to "not running"
SELF_HEAL_TOOLS = ("explain_failure", "get_trace", "list_errors", "get_run", "get_span")
# Only read-only tools are ever offered to the Builder, whatever this Studio advertises.
READ_ONLY_PREFIXES = ("get_", "list_", "explain_", "search_")


def is_read_only(tool_name: str) -> bool:
    return tool_name.startswith(READ_ONLY_PREFIXES)


class StudioClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.status = StudioStatus(ui_url=settings.studio_ui_url, run_url_template=settings.studio_run_url_template)
        self.tool_defs: list[dict[str, Any]] = []  # name, description, input_schema (read-only tools only)
        self._tracing_on = False
        self._misses = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _is_loopback(url: str) -> bool:
        host = httpx.URL(url).host
        return host in ("127.0.0.1", "localhost", "::1")

    async def probe(self) -> StudioStatus:
        """Is a local Studio alive? Never raises."""
        async with self._lock:
            s = self.settings
            alive = otlp = mcp = False
            tools: list[str] = []
            if self._is_loopback(s.studio_ui_url):
                try:
                    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as http:
                        r = await http.get(s.studio_ui_url.rstrip("/") + "/api/v1/runs", params={"limit": 1})
                        alive = r.status_code < 500
                        if alive and self._is_loopback(s.studio_otlp_url):
                            otlp = await self._otlp_ready(http)
                except (httpx.HTTPError, OSError):
                    alive = False
            if alive:
                self._misses = 0
            else:
                self._misses += 1
                if self.status.alive and self._misses < MISSES_BEFORE_DOWN:
                    return self.status  # one hiccup (Studio busy): keep what we knew until the next probe
            defs: list[dict[str, Any]] = []
            if alive and self._is_loopback(s.studio_mcp_url):
                found = await self._list_mcp_tools()
                defs = [d if isinstance(d, dict) else {"name": d, "description": "", "input_schema": {}} for d in found]
                tools = [d["name"] for d in defs]
                mcp = bool(tools)
            self.tool_defs = [d for d in defs if is_read_only(d["name"])]
            if otlp and not self._tracing_on:
                self._enable_tracing()
            self.status = StudioStatus(
                alive=alive,
                ui_url=s.studio_ui_url if alive else None,
                otlp=otlp,
                mcp=mcp,
                mcp_tools=tools,
                checked_at=utcnow(),
                tracing=self._tracing_on,
                run_url_template=s.studio_run_url_template,
            )
            return self.status

    async def _otlp_ready(self, http: httpx.AsyncClient) -> bool:
        """Is Studio's trace ingest there? A POST of an EMPTY OTLP document: the route answers 200 and stores
        nothing. (A GET is not a test: Studio's SPA fallback answers 404 for any `v1/...` GET, so the old GET
        probe never saw the ingest and tracing was never enabled.)"""
        try:
            r = await http.post(self.settings.studio_otlp_url.rstrip("/") + "/v1/traces", json={"resourceSpans": []})
        except (httpx.HTTPError, OSError):
            return False
        return r.status_code in (200, 202, 400, 415)

    async def _list_mcp_tools(self) -> list[dict[str, Any]]:
        """Advertised tools with their schemas (the Builder turns read-only ones into tools)."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async def _go() -> list[dict[str, Any]]:
                async with streamablehttp_client(self.settings.studio_mcp_url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        res = await session.list_tools()
                        return [{"name": t.name, "description": t.description or "",
                                 "input_schema": t.inputSchema or {}} for t in res.tools]

            return await asyncio.wait_for(_go(), timeout=3)
        except Exception:
            return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Call one Studio MCP tool if it exists; None when Studio or the tool is absent."""
        if not self.status.mcp or name not in self.status.mcp_tools or not is_read_only(name):
            return None
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async def _go() -> str:
                async with streamablehttp_client(self.settings.studio_mcp_url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        res = await session.call_tool(name, arguments)
                        parts = [getattr(c, "text", "") for c in res.content]
                        return "\n".join(p for p in parts if p)

            return await asyncio.wait_for(_go(), timeout=60)
        except Exception as exc:
            log.warning("studio tool %s failed: %s", name, type(exc).__name__)
            return None

    async def explain_failure(self, trace_id: str | None) -> str | None:
        """Self-heal: ask Studio why a step failed. Uses whatever tools this Studio offers."""
        if not trace_id:
            return None
        text = await self.call_tool("explain_failure", {"run_id": trace_id})
        if text:
            return text
        trace = await self.call_tool("get_trace", {"run_id": trace_id})
        if trace:
            return trace[:4000]
        return await self.call_tool("list_errors", {})

    def run_url(self, trace_id: str | None) -> str | None:
        if not (self.status.alive and trace_id):
            return None
        return self.settings.studio_run_url_template.format(
            base=self.settings.studio_ui_url.rstrip("/"), trace_id=trace_id
        )

    def _enable_tracing(self) -> None:
        try:
            from app.gateways.studio.tracing import enable_otlp

            enable_otlp(self.settings.studio_otlp_url)
            self._tracing_on = True
            log.info("Husk Studio detected: tracing enabled")
        except Exception as exc:
            log.warning("could not enable tracing: %s", type(exc).__name__)
