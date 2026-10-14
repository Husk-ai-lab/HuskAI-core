"""husk-gateway client: the only door to paid AI, entitlement and usage.

Every call carries the signed-in user's Clerk access token (`Authorization: Bearer`), taken from `AccessTokens`
(refreshed through Clerk when it expires). The token is injected by `GatewayTransport`, below the OpenAI SDK, so
chat, decisions and plain calls all share one place for: the explicit User-Agent (Cloudflare), one renewal when
the gateway answers 401 "invalid token", and a client-side rate gate that keeps us under the gateway's
60 requests/minute/user. No token, prompt or document text is ever logged."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

import httpx

from app.gateways.clerk.oidc import USER_AGENT, ClerkError
from app.gateways.clerk.tokens import AccessTokens
from app.gateways.gateway import errors
from app.helpers.logging import get_logger
from app.helpers.settings import Settings

log = get_logger("gateway")


class RateGate:
    """Sliding window: at most `per_minute` requests in any 60 s. Waiters are served in order."""

    def __init__(self, per_minute: int = 55):
        self.per_minute = max(1, per_minute)
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:  # held while waiting: first come, first served
            while True:
                now = time.monotonic()
                while self._times and now - self._times[0] >= 60.0:
                    self._times.popleft()
                if len(self._times) < self.per_minute:
                    self._times.append(now)
                    return
                await asyncio.sleep(max(0.05, 60.0 - (now - self._times[0])))


def _expired(request: httpx.Request) -> httpx.Response:
    body = {"error": {"message": "session expired", "type": "unauthorized", "code": "session_expired"}}
    return httpx.Response(401, json=body, request=request)


def _unavailable(request: httpx.Request) -> httpx.Response:
    """Clerk could not be reached (offline, 5xx): the session is fine, so this must not end it."""
    body = {"error": {"message": "sign-in service unreachable", "type": "unavailable", "code": "unavailable"}}
    return httpx.Response(503, json=body, request=request)


class GatewayTransport(httpx.AsyncBaseTransport):
    def __init__(self, access: AccessTokens, user_id: str, gate: RateGate, inner: httpx.AsyncBaseTransport | None = None):
        self.access, self.user_id, self.gate = access, user_id, gate
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["User-Agent"] = USER_AGENT
        for renew in (False, True):
            await self.gate.acquire()
            try:
                token = await self.access.get(self.user_id, renew=renew)
            except ClerkError as exc:
                if exc.status >= 500:
                    return _unavailable(request)  # offline or Clerk down: keep the session, local data stays usable
                return _expired(request)  # a 401 the SDK does not retry; mapped to "sign in again" upstream
            request.headers["Authorization"] = f"Bearer {token}"
            response = await self.inner.handle_async_request(request)
            if response.status_code != 401 or renew:
                return response
            await response.aread()
            if "organisation" in response.text.lower():  # a new token would not help
                return response
            await response.aclose()
        return response  # pragma: no cover

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass
class Entitlement:
    allowed: bool
    status: str
    org_id: str | None = None


class GatewayClient:
    def __init__(self, settings: Settings, access: AccessTokens):
        self.settings, self.access = settings, access
        self._gates: dict[str, RateGate] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}

    @property
    def base_url(self) -> str:
        return self.settings.gateway_url.rstrip("/")

    def signed_in(self, user_id: str | None) -> bool:
        """The user can obtain an access token (a live one, or a refresh token to get one)."""
        if not user_id:
            return False
        return bool(self.access.store.get_access(user_id) or self.access.store.load_refresh(user_id))

    def http_for(self, user_id: str) -> httpx.AsyncClient:
        client = self._clients.get(user_id)
        if client is None:
            gate = self._gates.setdefault(user_id, RateGate(self.settings.gateway_rate_per_minute))
            client = httpx.AsyncClient(
                base_url=self.base_url, timeout=httpx.Timeout(120.0, connect=10.0),
                transport=GatewayTransport(self.access, user_id, gate), headers={"User-Agent": USER_AGENT},
            )
            self._clients[user_id] = client
        return client

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()

    async def _call(self, method: str, path: str, user_id: str, timeout: float = 15.0, **kw) -> httpx.Response:  # type: ignore[no-untyped-def]
        try:
            r = await self.http_for(user_id).request(method, path, timeout=timeout, **kw)
        except httpx.HTTPError as exc:
            log.warning("gateway %s failed: %s", path, type(exc).__name__)
            raise errors.GatewayError("errors.gateway_unavailable", status=503) from exc
        if r.status_code >= 400:
            err = errors.from_response(r)
            try:  # the gateway's message names fields or reasons, never the input (its contract): safe to keep
                detail = errors.message_of(r.json())[:200]
            except Exception:
                detail = ""
            log.warning("gateway %s %s -> %s %s", method, path, r.status_code, detail)
            raise err
        return r

    async def entitlement(self, user_id: str) -> Entitlement:
        data = (await self._call("GET", "/v1/entitlement", user_id)).json()
        return Entitlement(allowed=bool(data.get("allowed")), status=str(data.get("status", "none")), org_id=data.get("org_id"))

    async def me(self, user_id: str) -> None:
        """Registers the user and organisation in Neon (the gateway's job); idempotent."""
        await self._call("POST", "/v1/me", user_id)

    async def usage(self, user_id: str, days: int = 30) -> list[dict]:
        return (await self._call("GET", "/v1/usage", user_id, params={"days": days})).json().get("usage", [])
