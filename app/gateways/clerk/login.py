"""Hand-off between the system browser and the app window.

The browser finishes the Clerk login at /callback and sets no cookie. The app (webview or browser tab) holds a
random `poll_id` that only it knows and redeems it once: the poll response is what sets the session cookie in
the app's own window. A web page cannot redeem it (no `poll_id`, and writes are origin-checked)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

TTL_S = 600
MAX_PENDING = 5


@dataclass
class _Entry:
    state: str
    status: str = "pending"  # pending | ok | error
    user_id: str | None = None
    reason: str | None = None
    created: float = field(default_factory=time.time)


@dataclass
class PollResult:
    status: str
    user_id: str | None = None
    reason: str | None = None


class LoginBroker:
    def __init__(self) -> None:
        self._by_poll: dict[str, _Entry] = {}
        self._by_state: dict[str, str] = {}

    def begin(self, state: str) -> str:
        self._gc()
        while len(self._by_poll) >= MAX_PENDING:  # drop the oldest
            oldest = min(self._by_poll, key=lambda p: self._by_poll[p].created)
            self._drop(oldest)
        poll_id = secrets.token_urlsafe(32)
        self._by_poll[poll_id] = _Entry(state=state)
        self._by_state[state] = poll_id
        return poll_id

    def complete(self, state: str, user_id: str) -> None:
        entry = self._entry_for(state)
        if entry:
            entry.status, entry.user_id = "ok", user_id

    def fail(self, state: str | None, reason: str) -> None:
        entry = self._entry_for(state) if state else None
        if entry:
            entry.status, entry.reason = "error", reason

    def poll(self, poll_id: str) -> PollResult:
        self._gc()
        entry = self._by_poll.get(poll_id)
        if entry is None:
            return PollResult("error", reason="expired")
        if entry.status == "pending":
            return PollResult("pending")
        self._drop(poll_id)  # one-time: redeemed or reported once
        return PollResult(entry.status, user_id=entry.user_id, reason=entry.reason)

    def _entry_for(self, state: str) -> _Entry | None:
        poll_id = self._by_state.get(state)
        return self._by_poll.get(poll_id) if poll_id else None

    def _drop(self, poll_id: str) -> None:
        entry = self._by_poll.pop(poll_id, None)
        if entry:
            self._by_state.pop(entry.state, None)

    def _gc(self) -> None:
        now = time.time()
        for p in [p for p, e in self._by_poll.items() if now - e.created > TTL_S]:
            self._drop(p)
