"""Self-healing through Husk Studio (only when a local Studio with MCP is alive)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.gateways.studio.client import StudioClient
from app.helpers.logging import get_logger

log = get_logger("self_heal")
T = TypeVar("T")


async def with_self_heal(
    attempt: Callable[[str | None], Awaitable[T]],
    studio: StudioClient | None,
    trace_id: Callable[[], str | None],
) -> T:
    """Run `attempt(hint=None)`; on failure ask Studio for a diagnosis and retry once with it."""
    try:
        return await attempt(None)
    except Exception as first:
        if studio is None or not studio.status.mcp:
            raise
        diagnosis = await studio.explain_failure(trace_id())
        if not diagnosis:
            raise
        log.info("self-heal: retrying with Studio diagnosis (%d chars)", len(diagnosis))
        try:
            return await attempt(diagnosis[:3000])
        except Exception:
            raise first from None
