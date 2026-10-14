"""Husk Studio compatibility without code coupling."""

from __future__ import annotations

import pathlib

import httpx
import respx

from app.gateways.studio.client import StudioClient
from app.helpers.settings import Settings

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_no_code_coupling_with_studio() -> None:
    """Architecture rule 1: Husk never imports Husk Studio (husk_ai / husk_shared / husk_studio)."""
    for py in (ROOT / "app").rglob("*.py"):
        text = py.read_text("utf-8")
        for pkg in ("import husk_ai", "from husk_ai", "husk_shared", "husk_studio"):
            assert pkg not in text, f"{py} references {pkg}"


async def test_probe_when_studio_absent() -> None:
    s = Settings(HUSK_ENV="test", HUSK_STUDIO_UI_URL="http://127.0.0.1:9", HUSK_STUDIO_MCP_URL="http://127.0.0.1:9/mcp")
    st = await StudioClient(s).probe()
    assert not st.alive and not st.mcp
    assert StudioClient(s).run_url("abc") is None  # no link when Studio is not running


@respx.mock
async def test_probe_when_studio_alive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = Settings(HUSK_ENV="test", HUSK_STUDIO_UI_URL="http://127.0.0.1:7654", HUSK_STUDIO_OTLP_URL="http://127.0.0.1:7654")
    respx.get("http://127.0.0.1:7654/api/v1/runs").mock(return_value=httpx.Response(200, json=[]))
    respx.post("http://127.0.0.1:7654/v1/traces").mock(return_value=httpx.Response(200, json={"partialSuccess": {}}))
    client = StudioClient(s)

    async def fake_tools(self):  # type: ignore[no-untyped-def]
        return ["list_runs", "get_trace", "list_errors"]

    monkeypatch.setattr(StudioClient, "_list_mcp_tools", fake_tools)
    monkeypatch.setattr(StudioClient, "_enable_tracing", lambda self: None)
    st = await client.probe()
    assert st.alive and st.otlp and st.mcp and "get_trace" in st.mcp_tools
    assert client.run_url("0af7651916cd43dd8448eb211c80319c") == "http://127.0.0.1:7654/runs/0af7651916cd43dd8448eb211c80319c"


async def test_non_loopback_studio_is_refused() -> None:
    s = Settings(HUSK_ENV="test", HUSK_STUDIO_UI_URL="http://10.0.0.5:7654")
    st = await StudioClient(s).probe()
    assert not st.alive  # Studio is development-only and loopback-only


async def test_self_heal_retries_with_diagnosis() -> None:
    from app.core.agents.harness.self_heal import with_self_heal

    class FakeStudio:
        class status:  # noqa: N801
            mcp = True

        async def explain_failure(self, trace_id):  # type: ignore[no-untyped-def]
            return "La colonna 'Fornitore' è vuota: usa 'Codice fornitore'."

    hints = []

    async def attempt(hint):  # type: ignore[no-untyped-def]
        hints.append(hint)
        if hint is None:
            raise ValueError("colonna mancante")
        return "ok"

    assert await with_self_heal(attempt, FakeStudio(), lambda: "trace") == "ok"
    assert hints[0] is None and "Codice fornitore" in hints[1]
