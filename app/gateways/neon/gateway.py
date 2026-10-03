"""Neon `husk-core` connector — anonymous Neon Data API, read-only, no secret.

Husk reads exactly two public views over HTTPS (PostgREST-style), with NO Authorization
header and NO database credential on the customer's computer:

    public.husk_maintenance  ← ops.maintenance   (the maintenance switch)
    public.husk_releases     ← ops.releases      (version notices)

The views and the `anonymous` grants live in `husk_core_schema.sql`. Everything that needs a
verified identity (billing state, writing org/user ids) belongs to husk-gateway, not here.
The endpoint URL is public by design (`NEON_DATA_API_URL`): what it can read is limited by
the grants on those two views only.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.data.neon.models import Maintenance, Release
from app.helpers.logging import get_logger
from app.helpers.settings import Settings
from app.helpers.time import utcnow

log = get_logger("neon")

MAINTENANCE_VIEW = "husk_maintenance"
RELEASES_VIEW = "husk_releases"


class NeonGateway(Protocol):
    enabled: bool

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def active_maintenance(self) -> list[Maintenance]: ...
    async def releases(self, component: str) -> list[Release]: ...


class NullNeon:
    """Used when NEON_DATA_API_URL is missing (tests, offline development)."""

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

    def __init__(self, base_url: str, timeout_s: float = 8.0, http: httpx.AsyncClient | None = None):
        if not base_url.startswith("https://"):
            raise ValueError("NEON_DATA_API_URL must be an https:// URL")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._http = http

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            # No auth header, ever: requests reach the database as the `anonymous` role.
            self._http = httpx.AsyncClient(timeout=self.timeout_s, headers={"Accept": "application/json"})
        return self._http

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _get(self, view: str, params: dict[str, str]) -> list[dict[str, Any]]:
        r = await self.http.get(f"{self.base_url}/{view}", params=params)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(f"unexpected Data API response for {view}")
        return data

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
    if settings.neon_data_api_url:
        return DataApiNeon(settings.neon_data_api_url)
    return NullNeon()
