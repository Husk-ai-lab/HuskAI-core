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

SELF_HEAL_TOOLS = ("explain_failure", "get_trace", "list_errors", "get_run", "get_span")


class StudioClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.status = StudioStatus(ui_url=settings.studio_ui_url)
        self._tracing_on = False
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
                    async with httpx.AsyncClient(timeout=0.8) as http:
                        r = await http.get(s.studio_ui_url.rstrip("/") + "/api/v1/runs", params={"limit": 1})
                        alive = r.status_code < 500
                        if alive and self._is_loopback(s.studio_otlp_url):
                            # A GET on the ingest answers 405 when the route exists.
                            r2 = await http.get(s.studio_otlp_url.rstrip("/") + "/v1/traces")
                            otlp = r2.status_code in (200, 400, 405, 415)
                except (httpx.HTTPError, OSError):
                    alive = False
            if alive and self._is_loopback(s.studio_mcp_url):
                tools = await self._list_mcp_tools()
                mcp = bool(tools)
            self.status = StudioStatus(
                alive=alive,
                ui_url=s.studio_ui_url if alive else None,
                otlp=otlp,
                mcp=mcp,
                mcp_tools=tools,
                checked_at=utcnow(),
            )
            if otlp and not self._tracing_on:
                self._enable_tracing()
            return self.status

    async def _list_mcp_tools(self) -> list[str]:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async def _go() -> list[str]:
                async with streamablehttp_client(self.settings.studio_mcp_url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        res = await session.list_tools()
                        return [t.name for t in res.tools]

            return await asyncio.wait_for(_go(), timeout=3)
        except Exception:
            return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Call one Studio MCP tool if it exists; None when Studio or the tool is absent."""
        if not self.status.mcp or name not in self.status.mcp_tools:
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
