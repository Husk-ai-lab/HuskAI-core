"""ZITADEL login only: PKCE, claims → identity, callback creates a session, no guest access."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.gateways.zitadel.oidc import (
    ORG_ID_CLAIM,
    ZitadelClient,
    ZitadelError,
    identity_from_claims,
    pkce_pair,
)
from app.helpers.settings import Settings
from app.main import create_app

ISSUER = "https://husk-test.zitadel.cloud"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/oauth/v2/authorize",
    "token_endpoint": f"{ISSUER}/oauth/v2/token",
    "jwks_uri": f"{ISSUER}/oauth/v2/keys",
    "end_session_endpoint": f"{ISSUER}/oidc/v1/end_session",
}


def test_pkce_pair() -> None:
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128


def test_identity_from_claims() -> None:
    ident = identity_from_claims({
        "sub": "u1", ORG_ID_CLAIM: "o1", "email": "a@b.it", "name": "Op",
        "urn:zitadel:iam:org:project:roles": {"admin": {"o1": "x"}},
    })
    assert (ident.user_id, ident.org_id, ident.roles) == ("u1", "o1", ["admin"])
    with pytest.raises(ZitadelError):
        identity_from_claims({"sub": "u1"})  # no org = refused (B2B: every user belongs to an org)


def test_protected_routes_require_login(settings, services) -> None:  # type: ignore[no-untyped-def]
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        for path in ("/api/v1/user/me", "/api/v1/documents", "/api/v1/automations", "/api/v1/discovery/state"):
            assert c.get(path).status_code == 401, path
        assert c.get("/health").status_code == 200
        assert c.get("/api/v1/system/status").status_code == 200  # maintenance shown before login


@respx.mock
def test_login_flow(tmp_path, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(HUSK_ENV="test", HUSK_DATA_DIR=tmp_path / "d", ZITADEL_ISSUER=ISSUER,
                        ZITADEL_CLIENT_ID="client-123", HUSK_PORT=8765)
    services.settings = settings
    services.zitadel = ZitadelClient(settings)
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(return_value=httpx.Response(200, json=DISCOVERY))
    respx.post(DISCOVERY["token_endpoint"]).mock(return_value=httpx.Response(200, json={
        "access_token": "at", "id_token": "idt", "refresh_token": "rt", "expires_in": 3600}))

    async def fake_verify(self, id_token, nonce=None):  # type: ignore[no-untyped-def]
        return {"sub": "zitadel-user-1", ORG_ID_CLAIM: "zitadel-org-1", "email": "mario@officine.it", "name": "Mario",
                "urn:zitadel:iam:user:resourceowner:name": "Officine Esempio"}

    monkeypatch.setattr(ZitadelClient, "verify_id_token", fake_verify)
    with TestClient(create_app(settings, services), base_url="http://127.0.0.1:8765") as c:
        url = c.get("/api/v1/user/auth/login", params={"login_hint": "mario@officine.it"}).json()["url"]
        q = parse_qs(urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"] and q["login_hint"] == ["mario@officine.it"]
        assert q["redirect_uri"] == ["http://127.0.0.1:8765/auth/callback"]
        assert "urn:zitadel:iam:user:resourceowner" in q["scope"][0]
        r = c.get("/auth/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/"
        me = c.get("/api/v1/user/me").json()
        assert me["user"]["org_id"] == "zitadel-org-1" and me["user"]["org_name"] == "Officine Esempio"
        # state is single-use
        r2 = c.get("/auth/callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
        assert "error=failed" in r2.headers["location"]
        assert c.post("/api/v1/user/auth/logout").status_code == 200
        assert c.get("/api/v1/user/me").status_code == 401


def test_no_guest_or_signup_routes(client) -> None:  # type: ignore[no-untyped-def]
    paths = client.get("/api/openapi.json").json()["paths"]
    assert not [p for p in paths if "guest" in p or "ospite" in p or "signup" in p or "register" in p]


def test_settings_roundtrip(client) -> None:  # type: ignore[no-untyped-def]
    r = client.patch("/api/v1/user/settings", json={"theme": "dark", "personality": "esplicativo"}).json()
    assert r == {"theme": "dark", "language": "en", "personality": "esplicativo"}
    assert client.get("/api/v1/user/me").json()["settings"]["theme"] == "dark"
