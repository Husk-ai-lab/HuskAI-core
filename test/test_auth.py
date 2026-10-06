"""Clerk login only: PKCE, token verification, browser → app hand-off, no guest access, secrets never stored/logged."""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.gateways.clerk.oidc import USER_AGENT, ClerkClient, ClerkError, TokenSet, pkce_pair
from app.helpers.settings import Settings
from app.main import create_app

ISSUER = "https://husk-test.clerk.accounts.dev"
CLIENT = "client-123"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/oauth/authorize",
    "token_endpoint": f"{ISSUER}/oauth/token",
    "userinfo_endpoint": f"{ISSUER}/oauth/userinfo",
    "revocation_endpoint": f"{ISSUER}/oauth/token/revoke",
    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS = {"keys": [{**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "alg": "RS256", "use": "sig"}]}
ACCESS = "SECRET-ACCESS-TOKEN-VALUE"  # replaced by a real JWT in `_token`; only used as a marker below


def _token(org: str | None = "org_clerk_1", *, iss: str = ISSUER, client: str = CLIENT, key=KEY, exp_in: int = 3600) -> str:  # type: ignore[no-untyped-def]
    claims = {"sub": "user_clerk_1", "client_id": client, "iss": iss, "iat": int(time.time()), "exp": int(time.time()) + exp_in}
    if org:
        claims["org_id"] = org
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1", "typ": "at+jwt"})


def _clerk_settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(HUSK_ENV="test", HUSK_DATA_DIR=tmp_path / "d", CLERK_ISSUER=ISSUER, CLERK_CLIENT_ID=CLIENT,
                    HUSK_PORT=8765, HUSK_OPEN_BROWSER=False, NEON_DATA_API_URL=None)


def _mock_clerk(access: str, refresh: str | None = "refresh-token-value") -> dict[str, respx.Route]:
    routes = {
        "discovery": respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(return_value=httpx.Response(200, json=DISCOVERY)),
        "jwks": respx.get(DISCOVERY["jwks_uri"]).mock(return_value=httpx.Response(200, json=JWKS)),
        "token": respx.post(DISCOVERY["token_endpoint"]).mock(return_value=httpx.Response(200, json={
            "access_token": access, "refresh_token": refresh, "expires_in": 86400})),
        "userinfo": respx.get(DISCOVERY["userinfo_endpoint"]).mock(return_value=httpx.Response(200, json={
            "email": "mario@officine.it", "name": "Mario"})),
        "revoke": respx.post(DISCOVERY["revocation_endpoint"]).mock(return_value=httpx.Response(200)),
    }
    return routes


def _start(c: TestClient) -> tuple[dict, dict]:
    started = c.post("/api/v1/user/auth/login").json()
    return started, parse_qs(urlparse(started["url"]).query)


def test_pkce_pair() -> None:
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128


@respx.mock
async def test_access_token_is_verified_against_the_jwks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _mock_clerk("x")
    clerk = ClerkClient(_clerk_settings(tmp_path))
    assert (await clerk.verify_access_token(_token()))["org_id"] == "org_clerk_1"
    for bad in (_token(iss="https://evil.example"), _token(client="other-client"), _token(key=OTHER_KEY), _token(exp_in=-3600)):
        with pytest.raises(ClerkError):
            await clerk.verify_access_token(bad)


@respx.mock
async def test_identity_needs_an_organisation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _mock_clerk("x")
    clerk = ClerkClient(_clerk_settings(tmp_path))
    ok = await clerk.identity(TokenSet("t", None, time.time() + 60, {"sub": "u1", "org_id": "o1"}))
    assert (ok.user_id, ok.org_id, ok.email) == ("u1", "o1", "mario@officine.it")
    with pytest.raises(ClerkError) as exc:
        await clerk.identity(TokenSet("t", None, time.time() + 60, {"sub": "u1"}))  # no org = refused
    assert exc.value.key == "errors.login_no_org"


def test_protected_routes_require_login(settings, services) -> None:  # type: ignore[no-untyped-def]
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        for path in ("/api/v1/user/me", "/api/v1/documents", "/api/v1/automations", "/api/v1/discovery/state"):
            assert c.get(path).status_code == 401, path
        assert c.get("/health").status_code == 200
        assert c.get("/api/v1/system/status").status_code == 200  # maintenance shown before login


@respx.mock
def test_login_flow_hands_the_session_to_the_app_window(tmp_path, services) -> None:  # type: ignore[no-untyped-def]
    settings = _clerk_settings(tmp_path)
    services.settings = settings
    services.clerk = ClerkClient(settings)
    access = _token()
    routes = _mock_clerk(access)
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        started, q = _start(c)
        assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT]
        assert q["redirect_uri"] == ["http://127.0.0.1:8765/callback"]
        assert "offline_access" in q["scope"][0] and "user:org:read" in q["scope"][0]
        assert "login_hint" not in q
        assert c.post("/api/v1/user/auth/poll", json={"poll_id": started["poll_id"]}).json() == {"status": "pending", "reason": None}

        # the system browser finishes the login: a redirect to the "done" page and NO cookie
        r = c.get("/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/auth/done?status=ok"
        assert "set-cookie" not in r.headers
        assert c.get("/api/v1/user/me").status_code == 401  # the browser's cookie jar has no session

        # the app window redeems the poll id once and receives the cookie
        r = c.post("/api/v1/user/auth/poll", json={"poll_id": started["poll_id"]})
        assert r.json()["status"] == "ok" and "husk_session" in r.headers["set-cookie"]
        me = c.get("/api/v1/user/me").json()
        assert (me["user"]["id"], me["user"]["org_id"], me["user"]["email"]) == ("user_clerk_1", "org_clerk_1", "mario@officine.it")
        again = c.post("/api/v1/user/auth/poll", json={"poll_id": started["poll_id"]}).json()
        assert again["status"] == "error" and again["reason"] == "expired"  # one-time

        # the state is single-use
        r2 = c.get("/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
        assert r2.headers["location"] == "/auth/done?status=error&reason=failed"

        # Cloudflare rejects Python's default User-Agent on the token endpoint
        assert routes["token"].calls.last.request.headers["user-agent"] == USER_AGENT
        assert routes["jwks"].calls.last.request.headers["user-agent"] == USER_AGENT

        assert c.post("/api/v1/user/auth/logout").status_code == 200
        assert routes["revoke"].called
        assert c.get("/api/v1/user/me").status_code == 401


@respx.mock
def test_no_organisation_and_cancelled_are_reported_to_the_app(tmp_path, services) -> None:  # type: ignore[no-untyped-def]
    settings = _clerk_settings(tmp_path)
    services.settings = settings
    services.clerk = ClerkClient(settings)
    _mock_clerk(_token(org=None))
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        started, q = _start(c)
        r = c.get("/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
        assert r.headers["location"] == "/auth/done?status=error&reason=no_org"
        assert c.post("/api/v1/user/auth/poll", json={"poll_id": started["poll_id"]}).json() == {"status": "error", "reason": "no_org"}
        assert c.get("/api/v1/user/me").status_code == 401

        started, q = _start(c)
        r = c.get("/callback", params={"error": "access_denied", "state": q["state"][0]}, follow_redirects=False)
        assert r.headers["location"] == "/auth/done?status=error&reason=cancelled"
        assert c.post("/api/v1/user/auth/poll", json={"poll_id": started["poll_id"]}).json()["reason"] == "cancelled"


def test_a_web_page_cannot_redeem_or_start_a_login(tmp_path, services) -> None:  # type: ignore[no-untyped-def]
    settings = _clerk_settings(tmp_path)
    services.settings = settings
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        evil = {"Origin": "https://evil.example"}
        assert c.post("/api/v1/user/auth/poll", json={"poll_id": "x" * 40}, headers=evil).status_code == 403
        assert c.post("/api/v1/user/auth/login", headers=evil).status_code == 403
        assert c.post("/api/v1/user/auth/poll", json={"poll_id": "x" * 40}).json()["reason"] == "expired"  # unknown id


@respx.mock
def test_tokens_are_never_persisted_or_logged(tmp_path, services, caplog) -> None:  # type: ignore[no-untyped-def]
    settings = _clerk_settings(tmp_path)
    services.settings = settings
    services.clerk = ClerkClient(settings)
    access = _token()
    _mock_clerk(access, refresh="refresh-token-value")
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        started, q = _start(c)
        c.get("/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
        c.post("/api/v1/user/auth/poll", json={"poll_id": started["poll_id"]})
    with services.db.read() as conn:
        dump = "\n".join(conn.iterdump())
    # (the test client's own request log, logger "httpx2", prints the callback URL: not application logging)
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx2")
    for secret in (access, "refresh-token-value", "abc", started["poll_id"]):
        assert secret not in logged
        assert secret not in dump
    assert services.tokens.load_refresh("user_clerk_1") == "refresh-token-value"  # kept in the keychain/memory store only


def test_no_guest_or_signup_routes(client) -> None:  # type: ignore[no-untyped-def]
    paths = client.get("/api/openapi.json").json()["paths"]
    assert not [p for p in paths if "guest" in p or "ospite" in p or "signup" in p or "register" in p]


def test_settings_roundtrip(client) -> None:  # type: ignore[no-untyped-def]
    r = client.patch("/api/v1/user/settings", json={"theme": "dark", "personality": "esplicativo"}).json()
    assert r == {"theme": "dark", "language": "en", "personality": "esplicativo", "connectors_onboarded": False}
    assert client.patch("/api/v1/user/settings", json={"connectors_onboarded": True}).json()["connectors_onboarded"] is True
    assert client.get("/api/v1/user/me").json()["settings"]["theme"] == "dark"
