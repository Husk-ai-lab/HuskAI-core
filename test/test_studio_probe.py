"""How Husk notices a running Husk Studio: the OTLP ingest is tested with a POST (Studio answers a GET on
/v1/traces with 404 through its SPA fallback), one missed probe does not mark a running Studio as gone, and the
probe is quick right after start (the shell starts Studio after Husk)."""

from __future__ import annotations

import httpx
import pytest

from app.gateways.studio import client as studio_mod
from app.gateways.studio.client import StudioClient
from app.helpers.settings import Settings
from app.main import studio_probe_interval


class FakeStudio:
    """What Studio v0.9.1 does: GET /v1/traces -> 404 (SPA fallback), POST /v1/traces -> 200."""

    def __init__(self) -> None:
        self.up = True
        self.get_traces_status = 404
        self.post_traces_status = 200
        self.posted: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("down")
        path = request.url.path
        if path == "/api/v1/runs":
            return httpx.Response(200, json=[])
        if path == "/v1/traces":
            if request.method == "POST":
                import json

                self.posted.append(json.loads(request.content))
                return httpx.Response(self.post_traces_status, json={"partialSuccess": {}})
            return httpx.Response(self.get_traces_status)
        return httpx.Response(404)


@pytest.fixture
def studio(monkeypatch: pytest.MonkeyPatch) -> tuple[StudioClient, FakeStudio]:
    fake = FakeStudio()
    real = httpx.AsyncClient

    def factory(*a, **kw):  # type: ignore[no-untyped-def]
        kw["transport"] = httpx.MockTransport(fake.handler)
        return real(*a, **kw)

    monkeypatch.setattr(studio_mod.httpx, "AsyncClient", factory)

    async def no_tools(self):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(StudioClient, "_list_mcp_tools", no_tools)
    enabled: list[str] = []
    monkeypatch.setattr(StudioClient, "_enable_tracing", lambda self: (enabled.append("on"), setattr(self, "_tracing_on", True)))
    settings = Settings(HUSK_ENV="test", NEON_DATA_API_URL=None)
    client = StudioClient(settings)
    client.enabled = enabled  # type: ignore[attr-defined]
    return client, fake


async def test_the_ingest_is_found_with_a_post_even_though_get_is_404(studio) -> None:  # type: ignore[no-untyped-def]
    client, fake = studio
    st = await client.probe()
    assert st.alive and st.otlp and st.tracing
    assert client.enabled == ["on"]  # type: ignore[attr-defined]
    assert fake.posted == [{"resourceSpans": []}]  # an empty document: nothing to store


async def test_a_studio_without_an_ingest_is_alive_but_not_traced(studio) -> None:  # type: ignore[no-untyped-def]
    client, fake = studio
    fake.post_traces_status = 404
    st = await client.probe()
    assert st.alive and not st.otlp and not st.tracing


async def test_one_missed_probe_keeps_a_running_studio_two_flip_it(studio) -> None:  # type: ignore[no-untyped-def]
    client, fake = studio
    assert (await client.probe()).alive
    fake.up = False
    assert (await client.probe()).alive  # first miss: still shown as running
    assert not (await client.probe()).alive  # second miss: gone
    fake.up = True
    assert (await client.probe()).alive  # and it comes back at once


async def test_a_studio_that_was_never_seen_is_not_kept_alive_by_hysteresis(studio) -> None:  # type: ignore[no-untyped-def]
    client, fake = studio
    fake.up = False
    assert not (await client.probe()).alive
    fake.up = True
    assert (await client.probe()).alive


def test_probe_cadence_is_quick_right_after_start_then_relaxed() -> None:
    assert studio_probe_interval(True, 5, 30) == 30
    assert studio_probe_interval(False, 5, 30) == 3
    assert studio_probe_interval(False, 400, 30) == 10
    assert studio_probe_interval(False, 5, 2) == 2  # never slower than the configured steady interval
