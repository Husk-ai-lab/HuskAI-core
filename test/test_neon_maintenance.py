"""Neon: maintenance switch through the anonymous Data API (no credential on the machine)."""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import uuid
from datetime import timedelta

import httpx
import pytest
import respx

from app.core.system.maintenance import MaintenanceMonitor
from app.data.neon import models as neon_models
from app.data.neon.models import Maintenance, Release
from app.gateways.neon import gateway as neon_gateway
from app.gateways.neon.gateway import DataApiNeon, NullNeon, build_neon
from app.helpers.settings import Settings
from app.helpers.time import utcnow

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = "https://ep-test.apirest.c-2.eu-central-1.aws.neon.tech/neondb/rest/v1"


class FakeNeon:
    enabled = True

    def __init__(self) -> None:
        self.windows: list[Maintenance] = []
        self.down = False

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def active_maintenance(self):  # type: ignore[no-untyped-def]
        if self.down:
            raise httpx.ConnectError("offline")
        return self.windows

    async def releases(self, component):  # type: ignore[no-untyped-def]
        return [Release(component="husk", channel="stable", version="0.2.0", min_version="0.1.0", assets={},
                        published_at=utcnow())]


def _window(blocking: bool, start_offset_min: int = -5, end_offset_min: int = 60) -> Maintenance:
    now = utcnow()
    return Maintenance(id=uuid.uuid4(), message_it="Aggiornamento in corso", message_en="Update in progress",
                       starts_at=now + timedelta(minutes=start_offset_min), ends_at=now + timedelta(minutes=end_offset_min),
                       blocking=blocking)


def _row(w: Maintenance) -> dict:
    return {"id": str(w.id), "message_it": w.message_it, "message_en": w.message_en,
            "starts_at": w.starts_at.isoformat(), "ends_at": w.ends_at.isoformat(), "blocking": w.blocking}


# --- monitor logic ----------------------------------------------------------------------
async def test_blocking_window_blocks(services) -> None:  # type: ignore[no-untyped-def]
    neon = FakeNeon()
    neon.windows = [_window(True)]
    mon = MaintenanceMonitor(neon, services.db, "0.1.0")
    st = await mon.check()
    assert st.active and st.blocking and mon.blocking and st.message_it == "Aggiornamento in corso"
    assert mon.release.update_available and not mon.release.update_required


async def test_non_blocking_and_future_windows(services) -> None:  # type: ignore[no-untyped-def]
    neon = FakeNeon()
    neon.windows = [_window(False)]
    mon = MaintenanceMonitor(neon, services.db, "0.1.0")
    await mon.check()
    assert mon.status.active and not mon.blocking
    neon.windows = [_window(True, start_offset_min=30, end_offset_min=90)]  # announced, not started
    await mon.check()
    assert not mon.status.active and not mon.blocking and mon.status.message_it


async def test_window_ends_on_time_without_a_new_poll(services) -> None:  # type: ignore[no-untyped-def]
    neon = FakeNeon()
    neon.windows = [_window(True)]
    mon = MaintenanceMonitor(neon, services.db, "0.1.0")
    await mon.check()
    assert mon.blocking
    mon.status.ends_at = utcnow() - timedelta(seconds=1)  # time passes
    assert not mon.blocking


async def test_all_upcoming_windows_listed_soonest_first(services) -> None:  # type: ignore[no-untyped-def]
    neon = FakeNeon()
    later = _window(False, start_offset_min=600, end_offset_min=660)
    soon = _window(True, start_offset_min=30, end_offset_min=90)
    now_w = _window(False)  # active, non-blocking
    neon.windows = [later, now_w, soon]
    mon = MaintenanceMonitor(neon, services.db, "0.1.0")
    st = await mon.check()
    assert st.active and not mon.blocking  # the current window drives the main fields
    assert [w.id for w in st.upcoming] == [str(soon.id), str(later.id)]
    cached = MaintenanceMonitor(neon, services.db, "0.1.0").status  # survives a restart
    assert len(cached.upcoming) == 2


async def test_desktop_notification_once_before_blocking_window(services) -> None:  # type: ignore[no-untyped-def]
    neon = FakeNeon()
    soon = _window(True, start_offset_min=8, end_offset_min=60)
    far = _window(True, start_offset_min=120, end_offset_min=180)
    quiet = _window(False, start_offset_min=5, end_offset_min=30)
    neon.windows = [soon, far, quiet]
    mon = MaintenanceMonitor(neon, services.db, "0.1.0")
    await mon.check()
    sent: list[tuple[str, str]] = []
    assert mon.notify_due(lambda t, m: sent.append((t, m))) == [str(soon.id)]
    assert "maintenance in" in sent[0][0] and sent[0][1] == "Update in progress"  # default language: English
    assert mon.notify_due(lambda t, m: sent.append((t, m))) == []  # once per window, even after restart
    assert MaintenanceMonitor(neon, services.db, "0.1.0").notify_due(lambda t, m: sent.append((t, m))) == []


async def test_desktop_notification_in_users_language(services) -> None:  # type: ignore[no-untyped-def]
    neon = FakeNeon()
    neon.windows = [_window(True, start_offset_min=5, end_offset_min=60)]
    mon = MaintenanceMonitor(neon, services.db, "0.1.0")
    await mon.check()
    sent: list[tuple[str, str]] = []
    mon.notify_due(lambda t, m: sent.append((t, m)), lang="it")
    assert "manutenzione tra" in sent[0][0] and sent[0][1] == "Aggiornamento in corso"


def test_system_status_exposes_upcoming_before_login(settings, services) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.data.models.system import MaintenanceWindow
    from app.main import create_app

    w = _window(True, start_offset_min=45, end_offset_min=90)
    services.maintenance.status.upcoming = [MaintenanceWindow(
        id=str(w.id), message_it=w.message_it, message_en=w.message_en, starts_at=w.starts_at, ends_at=w.ends_at,
        blocking=True)]
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:  # no session cookie
        up = c.get("/api/v1/system/status").json()["maintenance"]["upcoming"]
        assert up[0]["id"] == str(w.id) and up[0]["blocking"] is True


async def test_offline_uses_cache(services) -> None:  # type: ignore[no-untyped-def]
    neon = FakeNeon()
    neon.windows = [_window(True)]
    await MaintenanceMonitor(neon, services.db, "0.1.0").check()
    neon.down = True
    mon2 = MaintenanceMonitor(neon, services.db, "0.1.0")  # e.g. after a restart, network down
    await mon2.check()
    assert mon2.blocking and mon2.status.source == "cache"


async def test_not_configured_never_blocks(services) -> None:  # type: ignore[no-untyped-def]
    mon = MaintenanceMonitor(NullNeon(), services.db, "0.1.0")
    await mon.check()
    assert not mon.blocking and mon.status.source == "none"


# --- anonymous Data API client ---------------------------------------------------------------
@respx.mock
async def test_data_api_is_anonymous_and_read_only() -> None:
    w = _window(True)
    route = respx.get(f"{API}/husk_maintenance").mock(return_value=httpx.Response(200, json=[_row(w)]))
    neon = DataApiNeon(API)
    got = await neon.active_maintenance()
    assert got[0].id == w.id and got[0].blocking
    req = route.calls.last.request
    assert req.method == "GET"
    assert "authorization" not in {k.lower() for k in req.headers}  # no key, no token
    assert req.url.params["ends_at"].startswith("gt.") and req.url.params["order"] == "starts_at.asc"
    await neon.close()


@respx.mock
async def test_data_api_releases() -> None:
    route = respx.get(f"{API}/husk_releases").mock(return_value=httpx.Response(200, json=[{
        "component": "husk", "channel": "stable", "version": "0.3.0", "min_version": "0.1.0",
        "assets": {"aarch64-apple-darwin": {"url": "https://x", "sha256": "ab", "size": 1}},
        "published_at": utcnow().isoformat()}]))
    rels = await DataApiNeon(API).releases("husk")
    assert rels[0].version == "0.3.0" and route.calls.last.request.url.params["component"] == "eq.husk"


@respx.mock
async def test_data_api_errors_fall_back_to_cache(services) -> None:  # type: ignore[no-untyped-def]
    respx.get(f"{API}/husk_maintenance").mock(return_value=httpx.Response(200, json=[_row(_window(True))]))
    respx.get(f"{API}/husk_releases").mock(return_value=httpx.Response(200, json=[]))
    await MaintenanceMonitor(DataApiNeon(API), services.db, "0.1.0").check()
    respx.get(f"{API}/husk_maintenance").mock(return_value=httpx.Response(503))
    mon = MaintenanceMonitor(DataApiNeon(API), services.db, "0.1.0")
    await mon.check()
    assert mon.blocking and mon.status.source == "cache"


def test_data_api_requires_https_and_build() -> None:
    with pytest.raises(ValueError):
        DataApiNeon("http://insecure.example/rest/v1")
    assert isinstance(build_neon(Settings(HUSK_ENV="test", NEON_DATA_API_URL=API)), DataApiNeon)
    assert isinstance(build_neon(Settings(HUSK_ENV="test", NEON_DATA_API_URL=None)), NullNeon)


# --- API behaviour ---------------------------------------------------------------------------
def test_maintenance_blocks_ai_endpoints(client, services) -> None:  # type: ignore[no-untyped-def]
    services.maintenance.status = services.maintenance.status.model_copy(update={
        "active": True, "blocking": True, "message_it": "Manutenzione programmata"})
    r = client.post("/api/v1/discovery/runs")
    assert r.status_code == 503 and r.json()["code"] == "errors.maintenance"
    assert "Manutenzione programmata" in r.json()["detail"]  # message falls back to the language available
    assert client.get("/api/v1/system/status").json()["maintenance"]["blocking"] is True
    assert client.get("/api/v1/documents").status_code == 200  # local work continues


def test_me_exposes_no_billing(client) -> None:  # type: ignore[no-untyped-def]
    assert set(client.get("/api/v1/user/me").json()) == {"user", "settings"}


# --- boundaries ----------------------------------------------------------------------------------
def _code_only(source: str) -> str:
    """Source without docstrings and comments (ast.unparse drops comments)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant
        ) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_no_database_credential_anywhere() -> None:
    src = _code_only(inspect.getsource(neon_gateway))
    for forbidden in ("asyncpg", "postgres://", "postgresql://", "Authorization", "billing", "core.", "usage.", "INSERT"):
        assert forbidden not in src, forbidden
    for py in (ROOT / "app").rglob("*.py"):
        text = py.read_text("utf-8")
        assert "import asyncpg" not in text and "NEON_DATABASE_URL" not in text, py
    assert "asyncpg" not in (ROOT / "pyproject.toml").read_text()


def test_anonymous_grants_are_only_the_two_views() -> None:
    sql = (ROOT / "app" / "gateways" / "neon" / "husk_core_schema.sql").read_text()
    grants = [g for g in re.findall(r"GRANT[^;]+;", sql) if "anonymous" in g]
    assert grants == [
        "GRANT USAGE ON SCHEMA public TO anonymous;",
        "GRANT SELECT ON public.husk_maintenance, public.husk_releases TO anonymous;",
    ]
    assert "husk_app" not in sql


def test_neon_models_match_plan_schema() -> None:
    expected = {
        "core.organizations": ["id", "stripe_customer_id", "name", "created_at", "deleted_at"],
        "core.users": ["id", "org_id", "role", "created_at"],
        "billing.billing_state": ["org_id", "installation_paid_at", "status", "grace_until", "last_stripe_event_id", "updated_at"],
        "billing.stripe_events": ["event_id", "type", "received_at", "processed_at"],
        "usage.tokens_daily": ["org_id", "user_id", "day", "model", "tokens_in", "tokens_out", "calls"],
        "usage.meter_outbox": ["id", "org_id", "user_id", "model", "tokens_in", "tokens_out", "created_at", "sent_at"],
        "runtime.deployments": ["id", "org_id", "automation_id", "fly_app", "deployed_by", "status", "created_at", "stopped_at"],
        "runtime.runs": ["id", "deployment_id", "started_at", "ended_at", "status", "tokens", "error_code"],
        "ops.releases": ["component", "channel", "version", "min_version", "assets", "published_at"],
        "ops.maintenance": ["id", "message_it", "message_en", "starts_at", "ends_at", "blocking"],
        "audit.log": ["id", "org_id", "actor", "action", "at"],
    }
    found = {
        m.__table__: list(m.model_fields)
        for _, m in inspect.getmembers(neon_models, inspect.isclass)
        if hasattr(m, "__table__") and m.__module__ == neon_models.__name__ and "__table__" in m.__dict__
    }
    assert found == expected
