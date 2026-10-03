"""ZITADEL login: OpenID Connect authorisation code + PKCE (native app, no client secret).

Login only: there is no sign-up path in Husk. Self-registration must also be disabled
in the ZITADEL instance (see docs/ZITADEL_SETUP.md).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.helpers.logging import get_logger
from app.helpers.settings import Settings
from app.i18n.errors import LocalizedError

log = get_logger("zitadel")

ORG_ID_CLAIM = "urn:zitadel:iam:user:resourceowner:id"
ORG_NAME_CLAIM = "urn:zitadel:iam:user:resourceowner:name"
ROLES_CLAIM = "urn:zitadel:iam:org:project:roles"

SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "urn:zitadel:iam:user:resourceowner",
]


class ZitadelError(LocalizedError):
    status = 503


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class PendingLogin:
    verifier: str
    nonce: str
    created: float = field(default_factory=time.time)


@dataclass
class TokenSet:
    access_token: str
    id_token: str
    refresh_token: str | None
    expires_at: float
    claims: dict[str, Any]


@dataclass
class Identity:
    user_id: str
    org_id: str
    org_name: str | None
    email: str | None
    name: str | None
    roles: list[str]


class ZitadelClient:
    PENDING_TTL_S = 600

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self._http = http
        self._discovery: dict[str, Any] | None = None
        self._jwks: jwt.PyJWKClient | None = None
        self._pending: dict[str, PendingLogin] = {}

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15)
        return self._http

    def _require(self) -> None:
        if not self.settings.zitadel_configured:
            raise ZitadelError("errors.login_not_configured")

    async def discovery(self) -> dict[str, Any]:
        self._require()
        if self._discovery is None:
            url = self.settings.zitadel_issuer.rstrip("/") + "/.well-known/openid-configuration"
            r = await self.http.get(url)
            r.raise_for_status()
            self._discovery = r.json()
        return self._discovery

    def _scopes(self) -> str:
        scopes = list(SCOPES)
        if self.settings.zitadel_project_id:
            scopes.append(f"urn:zitadel:iam:org:project:id:{self.settings.zitadel_project_id}:aud")
            scopes.append("urn:zitadel:iam:org:projects:roles")
        return " ".join(scopes)

    async def authorize_url(self, login_hint: str | None = None) -> tuple[str, str]:
        disc = await self.discovery()
        self._gc()
        state = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        nonce = secrets.token_urlsafe(16)
        self._pending[state] = PendingLogin(verifier=verifier, nonce=nonce)
        params = {
            "client_id": self.settings.zitadel_client_id,
            "redirect_uri": self.settings.redirect_uri,
            "response_type": "code",
            "scope": self._scopes(),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{disc['authorization_endpoint']}?{urlencode(params)}", state

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, v in self._pending.items() if now - v.created > self.PENDING_TTL_S]:
            self._pending.pop(k, None)

    async def exchange(self, code: str, state: str) -> TokenSet:
        pending = self._pending.pop(state, None)
        if pending is None:
            raise ZitadelError("errors.login_state_expired", status=400)
        disc = await self.discovery()
        r = await self.http.post(
            disc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.redirect_uri,
                "client_id": self.settings.zitadel_client_id,
                "code_verifier": pending.verifier,
            },
        )
        if r.status_code != 200:
            log.warning("token exchange failed status=%s", r.status_code)
            raise ZitadelError("errors.login_failed", status=400)
        tokens = r.json()
        claims = await self.verify_id_token(tokens["id_token"], nonce=pending.nonce)
        return TokenSet(
            access_token=tokens["access_token"],
            id_token=tokens["id_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=time.time() + int(tokens.get("expires_in", 3600)),
            claims=claims,
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        disc = await self.discovery()
        r = await self.http.post(
            disc["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.settings.zitadel_client_id,
                "scope": self._scopes(),
            },
        )
        if r.status_code != 200:
            raise ZitadelError("errors.session_expired", status=401)
        tokens = r.json()
        claims = await self.verify_id_token(tokens["id_token"]) if tokens.get("id_token") else {}
        return TokenSet(
            access_token=tokens["access_token"],
            id_token=tokens.get("id_token", ""),
            refresh_token=tokens.get("refresh_token", refresh_token),
            expires_at=time.time() + int(tokens.get("expires_in", 3600)),
            claims=claims,
        )

    async def verify_id_token(self, id_token: str, nonce: str | None = None) -> dict[str, Any]:
        disc = await self.discovery()
        if self._jwks is None:
            self._jwks = jwt.PyJWKClient(disc["jwks_uri"], cache_keys=True)
        import anyio

        key = await anyio.to_thread.run_sync(self._jwks.get_signing_key_from_jwt, id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256", "ES256", "EdDSA"],
            audience=self.settings.zitadel_client_id,
            issuer=disc["issuer"],
            options={"require": ["exp", "iat", "sub"]},
        )
        if nonce is not None and claims.get("nonce") != nonce:
            raise ZitadelError("errors.login_failed", status=400)
        return claims

    async def end_session_url(self, id_token_hint: str | None, post_logout: str) -> str | None:
        disc = await self.discovery()
        ep = disc.get("end_session_endpoint")
        if not ep:
            return None
        params = {"post_logout_redirect_uri": post_logout, "client_id": self.settings.zitadel_client_id}
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{ep}?{urlencode(params)}"


def identity_from_claims(claims: dict[str, Any]) -> Identity:
    org_id = claims.get(ORG_ID_CLAIM)
    if not org_id:
        raise ZitadelError("errors.login_no_org", status=400)
    roles: list[str] = []
    for k, v in claims.items():
        if k == ROLES_CLAIM or (k.startswith("urn:zitadel:iam:org:project:") and k.endswith(":roles")):
            if isinstance(v, dict):
                roles.extend(v.keys())
    return Identity(
        user_id=claims["sub"],
        org_id=org_id,
        org_name=claims.get(ORG_NAME_CLAIM),
        email=claims.get("email"),
        name=claims.get("name") or claims.get("given_name"),
        roles=sorted(set(roles)),
    )
