"""Husk asks the desktop shell to open its installation manager: the shell pulls, nothing but an allow-listed
action travels, and without a shell nothing is queued."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.core import shell_bridge
from app.core.shell_bridge import ShellBridge
from app.main import create_app

SECRET = "launch-secret-for-tests"
H = {"X-Husk-Launch-Secret": SECRET}


# --- the bridge ---------------------------------------------------------------------------------------
async def test_nothing_is_queued_without_an_attached_shell() -> None:
    b = ShellBridge()
    assert not b.attached and await b.post("open_manager") is None
    await b.wait(0, 0)  # a poll attaches it
    assert b.attached
    r = await b.post("open_manager", "components", "studio")
    assert r is not None and r.id == 1 and r.component == "studio"


async def test_attachment_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    b = ShellBridge()
    await b.wait(0, 0)
    t0 = time.monotonic()
    monkeypatch.setattr(shell_bridge.time, "monotonic", lambda: t0 + shell_bridge.ATTACH_WINDOW_S + 1)
    assert not b.attached and await b.post("open_manager") is None


async def test_a_double_click_is_one_request_and_the_queue_is_capped() -> None:
    b = ShellBridge()
    await b.wait(0, 0)
    first = await b.post("open_manager", "components", "studio")
    again = await b.post("open_manager", "components", "studio")
    assert first is not None and again is not None and first.id == again.id
    other = await b.post("open_manager", "logs")
    assert other is not None and other.id == first.id + 1
    for i in range(40):  # different tabs/components cannot flood: ring of 20
        await b.post("open_manager", "updates", "husk" if i % 2 else None)
    assert len(await b.wait(0, 0)) <= shell_bridge.MAX_QUEUE


async def test_after_is_the_acknowledgement_and_wait_wakes_up_at_once() -> None:
    b = ShellBridge()
    await b.wait(0, 0)
    first = await b.post("open_manager")
    assert first is not None
    assert [r.id for r in await b.wait(0, 0)] == [first.id]
    assert await b.wait(first.id, 0) == []  # seen: not repeated
    t0 = time.monotonic()
    waiter = asyncio.create_task(b.wait(first.id, 5))
    await asyncio.sleep(0.05)
    second = await b.post("open_manager", "logs")
    got = await waiter
    assert [r.id for r in got] == [second.id] and time.monotonic() - t0 < 1.0


async def test_an_idle_poll_returns_empty_after_the_wait() -> None:
    b = ShellBridge()
    t0 = time.monotonic()
    assert await b.wait(0, 0.2) == []
    assert 0.15 < time.monotonic() - t0 < 1.0


# --- the endpoints ------------------------------------------------------------------------------------
@pytest.fixture
def api(settings, services):  # type: ignore[no-untyped-def]
    st = settings.model_copy(update={"launch_secret": SECRET})
    services.settings = st
    app = create_app(st, services)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_without_a_shell_the_ui_gets_the_fallback_and_nothing_is_stored(api, services) -> None:  # type: ignore[no-untyped-def]
    r = api.post("/api/v1/system/shell/open-manager", json={"tab": "components", "component": "studio"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] is False and body["shell_attached"] is False
    assert body["fallback_url"] == "https://github.com/Husk-ai-lab/husk-ai"
    assert api.get("/internal/shell/requests", headers=H).json()["requests"] == []  # not queued for later
    assert api.get("/api/v1/system/status").json()["shell"] == {"attached": True}  # the poll above attached it


def test_a_polling_shell_receives_the_request(api) -> None:  # type: ignore[no-untyped-def]
    first = api.get("/internal/shell/requests", headers=H, params={"after": 0, "wait": 0}).json()
    assert first == {"latest": 0, "requests": []}
    q = api.post("/api/v1/system/shell/open-manager", json={"component": "studio"}).json()
    assert q["queued"] is True and q["shell_attached"] is True and q["id"] == 1
    got = api.get("/internal/shell/requests", headers=H, params={"after": 0}).json()
    assert got["latest"] == 1
    (req,) = got["requests"]
    assert (req["id"], req["action"], req["tab"], req["component"]) == (1, "open_manager", "components", "studio")
    assert api.get("/internal/shell/requests", headers=H, params={"after": 1}).json()["requests"] == []


def test_the_internal_poll_needs_the_launch_secret(api) -> None:  # type: ignore[no-untyped-def]
    assert api.get("/internal/shell/requests").status_code == 403
    assert api.get("/internal/shell/requests", headers={"X-Husk-Launch-Secret": "nope"}).status_code == 403


def test_only_allow_listed_values_and_allowed_origins(api) -> None:  # type: ignore[no-untyped-def]
    assert api.post("/api/v1/system/shell/open-manager", json={"tab": "settings"}).status_code == 422
    assert api.post("/api/v1/system/shell/open-manager", json={"component": "../x"}).status_code == 422
    r = api.post("/api/v1/system/shell/open-manager", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403  # a web page cannot drive the shell
    assert api.get("/internal/shell/requests", headers=H, params={"wait": 99}).status_code == 422  # bounded wait
