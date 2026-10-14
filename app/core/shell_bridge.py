"""Requests from Husk to the desktop shell ("open the installation manager").

A Husk page cannot call the shell (the windows that show a backend get no IPC), so the shell PULLS: it long-polls
`GET /internal/shell/requests` (launch secret) and acts on what it finds. Nothing but an allow-listed action and two
enums ever travels: no URL, no command, no user data. See docs/DESKTOP_SHELL_INTEGRATION.md.

A request is only queued while a shell is attached (it polled in the last `ATTACH_WINDOW_S`): without a shell nothing
is stored, so nothing can fire later by surprise, and the UI falls back to the GitHub page."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from app.helpers.time import iso

Action = Literal["open_manager"]
Tab = Literal["components", "updates", "logs"]
Component = Literal["husk", "studio"]

ATTACH_WINDOW_S = 45.0  # a long poll lasts at most MAX_WAIT_S; this leaves room for one slow round trip
MAX_WAIT_S = 25.0
DEDUP_S = 3.0  # a double click is one request
MAX_QUEUE = 20


@dataclass(frozen=True)
class ShellRequest:
    id: int
    action: Action
    tab: Tab
    component: Component | None
    created_at: str
    at: float = field(default=0.0, repr=False, compare=False)  # monotonic, internal

    def public(self) -> dict:
        return {"id": self.id, "action": self.action, "tab": self.tab, "component": self.component,
                "created_at": self.created_at}


class ShellBridge:
    def __init__(self) -> None:
        self._items: deque[ShellRequest] = deque(maxlen=MAX_QUEUE)
        self._seq = 0
        self._last_poll: float | None = None
        self._cond = asyncio.Condition()

    @property
    def attached(self) -> bool:
        return self._last_poll is not None and time.monotonic() - self._last_poll < ATTACH_WINDOW_S

    @property
    def latest(self) -> int:
        return self._seq

    async def post(self, action: Action, tab: Tab = "components", component: Component | None = None) -> ShellRequest | None:
        """Queue a request; None when no shell is attached."""
        if not self.attached:
            return None
        now = time.monotonic()
        for r in reversed(self._items):
            if (r.action, r.tab, r.component) == (action, tab, component) and now - r.at < DEDUP_S:
                return r
        async with self._cond:
            self._seq += 1
            req = ShellRequest(self._seq, action, tab, component, iso(), at=now)
            self._items.append(req)
            self._cond.notify_all()
        return req

    def _after(self, after: int) -> list[ShellRequest]:
        return [r for r in self._items if r.id > after]

    async def wait(self, after: int, timeout: float) -> list[ShellRequest]:
        """Requests with `id > after`; waits up to `timeout` s (capped) when there are none. Counts as a poll."""
        self._last_poll = time.monotonic()
        timeout = max(0.0, min(float(timeout), MAX_WAIT_S))
        found = self._after(after)
        if found or timeout == 0:
            return found
        deadline = time.monotonic() + timeout
        async with self._cond:
            while not (found := self._after(after)):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._cond.wait(), remaining)
                except TimeoutError:
                    break
        self._last_poll = time.monotonic()
        return found
