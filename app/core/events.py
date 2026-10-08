"""In-process event hub: discovery progress and agent events streamed to the UI (SSE).

Every event gets a per-channel sequence number (`seq`), sent as the SSE `id`: a reconnecting client says where it
was (`Last-Event-ID`) and is replayed only what it missed, never the whole backlog again."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any


class EventHub:
    def __init__(self, backlog: int = 200):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._backlog: dict[str, deque] = defaultdict(lambda: deque(maxlen=backlog))
        self._seq: dict[str, int] = defaultdict(int)

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        self._seq[channel] += 1
        event = {**event, "seq": self._seq[channel]}
        self._backlog[channel].append(event)
        for q in list(self._subs[channel]):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def clear(self, channel: str) -> None:
        """A new attempt starts: what the previous one said (including its failed/paused event) is history.
        The sequence keeps counting, so ids stay increasing across attempts."""
        self._backlog[channel].clear()

    def reset(self) -> None:
        """All local data was deleted: no channel has history worth replaying."""
        self._backlog.clear()

    def subscribe(self, channel: str, replay: bool = True, after: int = 0) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        if replay:
            for e in self._backlog[channel]:
                if e["seq"] > after:
                    q.put_nowait(e)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)
