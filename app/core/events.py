"""In-process event hub: discovery progress and agent events streamed to the UI (SSE)."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any


class EventHub:
    def __init__(self, backlog: int = 200):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._backlog: dict[str, deque] = defaultdict(lambda: deque(maxlen=backlog))

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        self._backlog[channel].append(event)
        for q in list(self._subs[channel]):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self, channel: str, replay: bool = True) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        if replay:
            for e in self._backlog[channel]:
                q.put_nowait(e)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)
