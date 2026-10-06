"""Clerk login: OAuth 2.0 authorisation code + PKCE (public client, no secret), system browser + loopback.

Login only: there is no sign-up path in Husk. The access token is a plain RS256 JWT (claims `sub`, `org_id`,
`client_id`, `exp`); it is verified here against Clerk's JWKS and sent as Bearer to husk-gateway, which verifies
it again. Clerk sits behind Cloudflare, which rejects Python's default User-Agent on POSTs to /oauth/token
(error 1010): every call sends an explicit one, and the JWKS is fetched with httpx (never urllib).
See docs/CLERK_SETUP.md.
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

from app import __version__
from app.helpers.logging import get_logger
from app.helpers.settings import Settings
from app.i18n.errors import LocalizedError

log = get_logger("clerk")

USER_AGENT = f"Husk/{__version__}"
SCOPES = "openid profile email offline_access user:org:read"
JWKS_TTL_S = 3600


class ClerkError(LocalizedError):
    status = 503


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class PendingLogin:
    verifier: str
    created: float = field(default_factory=time.time)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float
    claims: dict[str, Any]


@dataclass
class Identity:
    user_id: str
    org_id: str
    email: str | None
    name: str | None


class ClerkClient:
    PENDING_TTL_S = 600

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self._http = http
        self._discovery: dict[str, Any] | None = None
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_at = 0.0
        self._pending: dict[str, PendingLogin] = {}

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT})
        return self._http

    @property
    def issuer(self) -> str:
        return (self.settings.clerk_issuer or "").rstrip("/")

    def _require(self) -> None:
        if not self.settings.clerk_configured:
            raise ClerkError("errors.login_not_configured")

    async def discovery(self) -> dict[str, Any]:
        self._require()
        if self._discovery is None:
            r = await self.http.get(self.issuer + "/.well-known/openid-configuration", headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            self._discovery = r.json()
        return self._discovery

    async def _endpoint(self, name: str, default: str) -> str:
        return (await self.discovery()).get(name) or self.issuer + default

    # --- login --------------------------------------------------------------------------
    async def authorize_url(self) -> tuple[str, str]:
        url = await self._endpoint("authorization_endpoint", "/oauth/authorize")
        self._gc()
        state = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        self._pending[state] = PendingLogin(verifier=verifier)
        params = {
            "client_id": self.settings.clerk_client_id,
            "redirect_uri": self.settings.redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{url}?{urlencode(params)}", state

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, v in self._pending.items() if now - v.created > self.PENDING_TTL_S]:
            self._pending.pop(k, None)

    async def exchange(self, code: str, state: str) -> TokenSet:
        pending = self._pending.pop(state, None)
        if pending is None or time.time() - pending.created > self.PENDING_TTL_S:
            raise ClerkError("errors.login_state_expired", status=400)
        return await self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
            "client_id": self.settings.clerk_client_id,
            "code_verifier": pending.verifier,
        }, failure=("errors.login_failed", 400))

    async def refresh(self, refresh_token: str) -> TokenSet:
        return await self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.settings.clerk_client_id,
        }, failure=("errors.session_expired", 401), keep_refresh=refresh_token)

    async def _token_request(self, data: dict[str, str], failure: tuple[str, int],
                             keep_refresh: str | None = None) -> TokenSet:
        url = await self._endpoint("token_endpoint", "/oauth/token")
        try:
            r = await self.http.post(url, data=data, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            log.warning("token request failed: %s", type(exc).__name__)
            raise ClerkError("errors.login_unreachable", status=503) from exc
        if r.status_code >= 500:
            log.warning("token request failed status=%s", r.status_code)
            raise ClerkError("errors.login_unreachable", status=503)
        if r.status_code != 200:
            log.warning("token request refused status=%s", r.status_code)
            raise ClerkError(failure[0], status=failure[1])
        body = r.json()
        claims = await self.verify_access_token(body["access_token"])
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or keep_refresh,
            expires_at=float(claims.get("exp") or time.time() + int(body.get("expires_in", 3600))),
            claims=claims,
        )

    # --- verification ---------------------------------------------------------------------
    async def _signing_key(self, token: str) -> jwt.PyJWK:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise ClerkError("errors.login_failed", status=400) from exc
        for force in (False, True):
            await self._load_keys(force)
            if kid in self._keys:
                return self._keys[kid]
        raise ClerkError("errors.login_failed", status=400)

    async def _load_keys(self, force: bool) -> None:
        if self._keys and not force and time.time() - self._keys_at < JWKS_TTL_S:
            return
        url = (await self.discovery()).get("jwks_uri") or self.issuer + "/.well-known/jwks.json"
        try:
            r = await self.http.get(url, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ClerkError("errors.login_unreachable", status=503) from exc
        self._keys = {k["kid"]: jwt.PyJWK.from_dict(k) for k in r.json().get("keys", []) if k.get("kid")}
        self._keys_at = time.time()

    async def verify_access_token(self, token: str) -> dict[str, Any]:
        key = await self._signing_key(token)
        try:
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"], issuer=self.issuer, leeway=30,
                options={"require": ["exp", "sub"], "verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            log.warning("access token rejected: %s", type(exc).__name__)
            raise ClerkError("errors.login_failed", status=400) from exc
        cid = claims.get("client_id")
        if cid and cid != self.settings.clerk_client_id:
            raise ClerkError("errors.login_failed", status=400)
        return claims

    async def identity(self, tokens: TokenSet) -> Identity:
        """Who signed in. `sub` and `org_id` come from the verified token; the email (not in the token)
        comes from userinfo, best effort."""
        org_id = tokens.claims.get("org_id")
        if not org_id:
            raise ClerkError("errors.login_no_org", status=400)
        email = name = None
        try:
            url = await self._endpoint("userinfo_endpoint", "/oauth/userinfo")
            r = await self.http.get(url, headers={"Authorization": f"Bearer {tokens.access_token}",
                                                  "User-Agent": USER_AGENT})
            if r.status_code == 200:
                info = r.json()
                email = info.get("email")
                name = info.get("name") or info.get("given_name")
        except Exception as exc:  # the email is nice to have; login must not depend on it
            log.warning("userinfo failed: %s", type(exc).__name__)
        return Identity(user_id=tokens.claims["sub"], org_id=org_id, email=email, name=name)

    async def revoke(self, token: str) -> None:
        """Best effort: the local copy is deleted either way."""
        try:
            url = await self._endpoint("revocation_endpoint", "/oauth/token/revoke")
            await self.http.post(url, data={"token": token, "client_id": self.settings.clerk_client_id},
                                 headers={"User-Agent": USER_AGENT})
        except Exception as exc:
            log.warning("token revoke failed: %s", type(exc).__name__)
