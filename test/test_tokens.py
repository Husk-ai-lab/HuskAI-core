"""The sign-in refresh token survives a restart (private file, no keychain), the newest token wins, and an unreachable
Clerk does not sign anyone out."""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import time
import types
from pathlib import Path

import pytest

from app.gateways.clerk.oidc import ClerkError
from app.gateways.clerk.tokens import AccessTokens, TokenStore
from app.gateways.sqlite.wipe import wipe_data
from app.helpers.paths import DataPaths
from app.helpers.settings import Settings
from test.conftest import USER, login


@pytest.fixture
def no_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("the keychain was touched")

    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_password=boom, set_password=boom, delete_password=boom))


def test_the_refresh_token_survives_a_restart_without_the_keychain(tmp_path: Path, no_keychain: None) -> None:
    f = tmp_path / "session-secrets.json"
    TokenStore(file=f).save_refresh(USER, "r-1")
    assert TokenStore(file=f).load_refresh(USER) == "r-1"  # a new instance = a new backend process
    if os.name != "nt":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert not (tmp_path / "session-secrets.json.tmp").exists()


def test_rotation_replaces_the_token_and_sign_out_removes_it(tmp_path: Path, no_keychain: None) -> None:
    f = tmp_path / "session-secrets.json"
    s = TokenStore(file=f)
    s.save_refresh(USER, "r-1")
    s.save_refresh(USER, "r-2")
    s.save_refresh("other", "r-x")
    assert TokenStore(file=f).load_refresh(USER) == "r-2"
    s.delete(USER)
    again = TokenStore(file=f)
    assert again.load_refresh(USER) is None and again.load_refresh("other") == "r-x"


def test_a_keychain_that_refuses_the_write_never_hides_the_newest_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsigned build: the old item can be read but not updated. The stale token must not win over the new one."""

    def refuse(*_a: object, **_k: object) -> None:
        raise RuntimeError("PasswordSetError")

    fake = types.SimpleNamespace(get_password=lambda *_a: "stale-token", set_password=refuse, delete_password=refuse)
    monkeypatch.setitem(sys.modules, "keyring", fake)
    s = TokenStore(use_keyring=True)
    s.save_refresh(USER, "fresh-token")
    assert s.load_refresh(USER) == "fresh-token"


def test_store_choice_follows_the_setting(tmp_path: Path) -> None:
    paths = DataPaths(root=tmp_path)
    base = {"HUSK_DATA_DIR": tmp_path, "NEON_DATA_API_URL": None}
    test_env = TokenStore.from_settings(Settings(HUSK_ENV="test", **base), paths)
    assert test_env.file is None and not test_env.use_keyring
    default = TokenStore.from_settings(Settings(HUSK_ENV="prod", **base), paths)
    assert default.file == paths.session_secrets and not default.use_keyring
    keychain = TokenStore.from_settings(Settings(HUSK_ENV="prod", HUSK_SECRET_STORE="keychain", **base), paths)
    assert keychain.file is None and keychain.use_keyring


class _Clerk:
    def __init__(self, error: ClerkError | None = None) -> None:
        self.error = error

    async def refresh(self, _tok: str):  # type: ignore[no-untyped-def]
        if self.error:
            raise self.error
        from app.gateways.clerk.oidc import TokenSet

        return TokenSet("access-2", "r-2", time.time() + 3600, {})


async def test_unreachable_clerk_is_not_a_sign_out_but_a_refusal_is(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateways.gateway.client import GatewayTransport, RateGate

    store = TokenStore(use_keyring=False)
    store.save_refresh(USER, "r-1")
    import httpx

    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("nothing may be sent without a token")

    req = httpx.Request("GET", "https://gw.test/v1/entitlement")
    down = GatewayTransport(AccessTokens(store, _Clerk(ClerkError("errors.login_unreachable", status=503))),  # type: ignore[arg-type]
                            USER, RateGate(100), inner=Inner())
    assert (await down.handle_async_request(req)).status_code == 503
    refused = GatewayTransport(AccessTokens(store, _Clerk(ClerkError("errors.session_expired", status=401))),  # type: ignore[arg-type]
                               USER, RateGate(100), inner=Inner())
    assert (await refused.handle_async_request(req)).status_code == 401
    nothing = GatewayTransport(AccessTokens(TokenStore(use_keyring=False), _Clerk()),  # type: ignore[arg-type]
                               USER, RateGate(100), inner=Inner())
    assert (await nothing.handle_async_request(req)).status_code == 401  # no refresh token at all


def test_offline_keeps_the_session_and_a_refusal_ends_it(client, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    services.tokens.save_refresh(USER, "r-1")  # signed in, but no live access token: the next call must refresh

    async def offline(_tok: str):  # type: ignore[no-untyped-def]
        raise ClerkError("errors.login_unreachable", status=503)

    monkeypatch.setattr(services.clerk, "refresh", offline)
    r = client.get("/api/v1/user/entitlement")
    assert r.status_code == 200 and r.json()["source"] in ("cache", "unknown")  # data stays usable
    assert client.get("/api/v1/user/me").status_code == 200  # still signed in

    async def refused(_tok: str):  # type: ignore[no-untyped-def]
        raise ClerkError("errors.session_expired", status=401)

    monkeypatch.setattr(services.clerk, "refresh", refused)
    assert client.get("/api/v1/user/entitlement").status_code == 401
    assert client.get("/api/v1/user/me").status_code == 401


def test_delete_all_data_keeps_the_sign_in_token(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    store = TokenStore(file=services.paths.session_secrets)
    store.save_refresh(USER, "r-1")
    wipe_data(services.db, services.paths)
    assert TokenStore(file=services.paths.session_secrets).load_refresh(USER) == "r-1"


async def test_the_keychain_never_blocks_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_get(*_a: object) -> str:
        time.sleep(0.4)  # a keychain prompt waiting for the person
        return "r-1"

    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(
        get_password=slow_get, set_password=lambda *_a: None, delete_password=lambda *_a: None))
    tokens = AccessTokens(TokenStore(use_keyring=True), _Clerk())  # type: ignore[arg-type]
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    assert await tokens.get(USER) == "access-2"
    task.cancel()
    assert ticks >= 5  # the loop kept running while the keychain call waited
