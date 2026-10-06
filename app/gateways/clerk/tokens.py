"""Refresh tokens live in the OS keychain (never in a plain file); access tokens in memory only.

`AccessTokens.get(user_id)` is what every gateway call uses: a valid access token, refreshed through Clerk
when it is about to expire (one refresh at a time per user)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.gateways.clerk.oidc import ClerkClient, ClerkError
from app.helpers.logging import get_logger

log = get_logger("tokens")
SERVICE = "ai.husk.desktop"
EXPIRY_MARGIN_S = 60


@dataclass
class _Access:
    token: str
    expires_at: float


class TokenStore:
    def __init__(self, use_keyring: bool = True):
        self.use_keyring = use_keyring
        self._mem_refresh: dict[str, str] = {}
        self._access: dict[str, _Access] = {}

    def save_refresh(self, user_id: str, token: str | None) -> None:
        if not token:
            return
        if self.use_keyring:
            try:
                import keyring

                keyring.set_password(SERVICE, user_id, token)
                return
            except Exception as exc:  # keychain unavailable (headless linux, CI)
                log.warning("keyring unavailable, keeping refresh token in memory: %s", type(exc).__name__)
        self._mem_refresh[user_id] = token

    def load_refresh(self, user_id: str) -> str | None:
        if self.use_keyring:
            try:
                import keyring

                tok = keyring.get_password(SERVICE, user_id)
                if tok:
                    return tok
            except Exception:
                pass
        return self._mem_refresh.get(user_id)

    def delete(self, user_id: str) -> None:
        self._access.pop(user_id, None)
        self._mem_refresh.pop(user_id, None)
        if self.use_keyring:
            try:
                import keyring

                keyring.delete_password(SERVICE, user_id)
            except Exception:
                pass

    def set_access(self, user_id: str, token: str, expires_at: float) -> None:
        self._access[user_id] = _Access(token, expires_at)

    def get_access(self, user_id: str) -> str | None:
        a = self._access.get(user_id)
        if a and a.expires_at - EXPIRY_MARGIN_S > time.time():
            return a.token
        return None

    def drop_access(self, user_id: str) -> None:
        self._access.pop(user_id, None)


class AccessTokens:
    def __init__(self, store: TokenStore, clerk: ClerkClient):
        self.store, self.clerk = store, clerk
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, user_id: str, renew: bool = False) -> str:
        """A valid access token for this user. `renew=True` drops the cached one first (the gateway said 401)."""
        if renew:
            self.store.drop_access(user_id)
        token = self.store.get_access(user_id)
        if token:
            return token
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            token = self.store.get_access(user_id)
            if token:
                return token
            refresh = self.store.load_refresh(user_id)
            if not refresh:
                raise ClerkError("errors.session_expired", status=401)
            ts = await self.clerk.refresh(refresh)
            self.store.save_refresh(user_id, ts.refresh_token)
            self.store.set_access(user_id, ts.access_token, ts.expires_at)
            return ts.access_token
