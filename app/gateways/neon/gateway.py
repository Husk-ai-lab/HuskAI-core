"""Neon `husk-core` connector — anonymous Neon Data API, read-only, no secret.

Husk reads exactly two public views over HTTPS (PostgREST-style) as the `anonymous` role, with
NO database credential on the customer's computer. The Data API only accepts JWTs, so each
call carries a short-lived *anonymous* token that Neon Auth hands out to anyone
(`GET <NEON_AUTH_URL>/token/anonymous`, role claim `anonymous`, no sign-in, no secret). It is
kept in memory only and refreshed before it expires:

    public.husk_maintenance  ← ops.maintenance   (the maintenance switch)
    public.husk_releases     ← ops.releases      (version notices)

The views and the `anonymous` grants live in `husk_core_schema.sql`. Everything that needs a
verified identity (billing state, writing org/user ids) belongs to husk-gateway, not here.
Both URLs are public by design (`NEON_DATA_API_URL`, `NEON_AUTH_URL`): what the anonymous role
can read is limited by the grants on those two views only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Protocol

import httpx

from app.data.neon.models import Maintenance, Release
from app.helpers.logging import get_logger
from app.helpers.settings import Settings
from app.helpers.time import utcnow

log = get_logger("neon")

MAINTENANCE_VIEW = "husk_maintenance"
RELEASES_VIEW = "husk_releases"
TOKEN_REFRESH_MARGIN_S = 60  # renew the anonymous token this long before it expires
TOKEN_DEFAULT_TTL_S = 300  # when the token carries no readable `exp`


def _token_expiry(token: str) -> float:
    """`exp` of a JWT (unverified: we only need it to schedule the refresh), else a short default."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, ValueError, TypeError):
        return time.time() + TOKEN_DEFAULT_TTL_S


class NeonGateway(Protocol):
    enabled: bool

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def active_maintenance(self) -> list[Maintenance]: ...
    async def releases(self, component: str) -> list[Release]: ...


class NullNeon:
    """Used when NEON_DATA_API_URL or NEON_AUTH_URL is missing (tests, offline development)."""

    enabled = False

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def active_maintenance(self) -> list[Maintenance]:
        return []

    async def releases(self, component: str) -> list[Release]:
        return []


class DataApiNeon:
    """Anonymous, read-only client for the Neon Data API."""

    enabled = True

    def __init__(self, base_url: str, auth_url: str, timeout_s: float = 8.0, http: httpx.AsyncClient | None = None):
        if not base_url.startswith("https://") or not auth_url.startswith("https://"):
            raise ValueError("NEON_DATA_API_URL and NEON_AUTH_URL must be https:// URLs")
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url.rstrip("/")
        self.timeout_s = timeout_s
        self._http = http
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = asyncio.Lock()

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout_s, headers={"Accept": "application/json"})
        return self._http

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._token = None

    async def _anonymous_token(self, *, renew: bool = False) -> str:
        """Short-lived `anonymous` JWT from Neon Auth. Memory only: never stored, never logged."""
        async with self._lock:  # concurrent polls fetch once
            if renew or self._token is None or time.time() >= self._token_exp - TOKEN_REFRESH_MARGIN_S:
                r = await self.http.get(f"{self.auth_url}/token/anonymous")
                r.raise_for_status()
                body = r.json()
                token = next((body[k] for k in ("token", "access_token", "jwt") if isinstance(body.get(k), str)), None)
                if not token:
                    raise ValueError("unexpected anonymous token response")
                self._token, self._token_exp = token, _token_expiry(token)
            return self._token

    async def _get(self, view: str, params: dict[str, str]) -> list[dict[str, Any]]:
        for attempt in (0, 1):
            token = await self._anonymous_token(renew=attempt == 1)
            r = await self.http.get(f"{self.base_url}/{view}", params=params,
                                    headers={"Authorization": f"Bearer {token}"})
            if r.status_code in (400, 401) and attempt == 0:
                continue  # expired or revoked token: fetch a new one, once
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise ValueError(f"unexpected Data API response for {view}")
            return data
        raise AssertionError("unreachable")

    async def active_maintenance(self) -> list[Maintenance]:
        rows = await self._get(MAINTENANCE_VIEW, {
            "select": "id,message_it,message_en,starts_at,ends_at,blocking",
            "ends_at": f"gt.{utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "order": "starts_at.asc",
        })
        return [Maintenance(**r) for r in rows]

    async def releases(self, component: str) -> list[Release]:
        rows = await self._get(RELEASES_VIEW, {
            "select": "component,channel,version,min_version,assets,published_at",
            "component": f"eq.{component}",
        })
        return [Release(**r) for r in rows]


def build_neon(settings: Settings) -> NeonGateway:
    if settings.neon_data_api_url and settings.neon_auth_url:
        return DataApiNeon(settings.neon_data_api_url, settings.neon_auth_url)
    return NullNeon()
