"""The sign-in refresh token lives in a private owner-only file in the data folder (default) or the OS keychain
(`HUSK_SECRET_STORE=keychain`); access tokens in memory only.

Why a file by default: a macOS keychain item is bound to the binary that created it, so an unsigned build is refused
(write AND read) after every rebuild or update, the refresh token is lost at the next start and the user is signed
out. The file store never touches the keychain, so it never prompts. Same trade-off and same switch as the
connector secrets (docs/CONNECTORS.md).

`AccessTokens.get(user_id)` is what every gateway call uses: a valid access token, refreshed through Clerk
when it is about to expire (one refresh at a time per user)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.gateways.clerk.oidc import ClerkClient, ClerkError
from app.helpers.logging import get_logger
from app.helpers.private_json import PrivateJsonFile

if TYPE_CHECKING:
    from app.helpers.paths import DataPaths
    from app.helpers.settings import Settings

log = get_logger("tokens")
SERVICE = "ai.husk.desktop"
EXPIRY_MARGIN_S = 60


@dataclass
class _Access:
    token: str
    expires_at: float


class TokenStore:
    """`file` given: private file store, the keychain is never touched. Otherwise `use_keyring` picks the OS
    keychain (True) or memory only (False, tests). Memory always holds the newest token and wins on read."""

    def __init__(self, use_keyring: bool = True, file: Path | None = None):
        self.use_keyring = use_keyring and file is None
        self.file = file
        self._store = PrivateJsonFile(file, "session secrets") if file is not None else None
        self._mem_refresh: dict[str, str] = {}
        self._access: dict[str, _Access] = {}

    @classmethod
    def from_settings(cls, settings: Settings, paths: DataPaths) -> TokenStore:
        """Tests (`env="test"`) keep tokens in memory, whatever the setting says."""
        if settings.env == "test":
            return cls(use_keyring=False)
        if settings.secret_store == "keychain":
            return cls(use_keyring=True)
        return cls(file=paths.session_secrets)

    def save_refresh(self, user_id: str, token: str | None) -> None:
        if not token:
            return
        self._mem_refresh[user_id] = token  # newest first: a store that refuses the write must not hide it
        if self._store is not None:
            try:
                with self._store.lock:
                    data = self._store.read()
                    data[user_id] = token
                    self._store.write(data)
            except OSError as exc:
                log.warning("session file not writable, keeping the refresh token in memory: %s", type(exc).__name__)
        elif self.use_keyring:
            try:
                import keyring

                keyring.set_password(SERVICE, user_id, token)
            except Exception as exc:  # keychain unavailable or refused (unsigned build, headless linux, CI)
                log.warning("keyring unavailable, keeping refresh token in memory: %s", type(exc).__name__)

    def load_refresh(self, user_id: str) -> str | None:
        tok = self._mem_refresh.get(user_id)
        if tok:
            return tok
        if self._store is not None:
            with self._store.lock:
                tok = self._store.read().get(user_id)
        elif self.use_keyring:
            try:
                import keyring

                tok = keyring.get_password(SERVICE, user_id)
            except Exception:
                tok = None
        if tok:
            self._mem_refresh[user_id] = tok
        return tok or None

    def delete(self, user_id: str) -> None:
        self._access.pop(user_id, None)
        self._mem_refresh.pop(user_id, None)
        if self._store is not None:
            try:
                with self._store.lock:
                    data = self._store.read()
                    if data.pop(user_id, None) is not None:
                        self._store.write(data)
            except OSError as exc:
                log.warning("session file not writable: %s", type(exc).__name__)
        elif self.use_keyring:
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
        """A valid access token for this user. `renew=True` drops the cached one first (the gateway said 401).

        Raises `ClerkError` 401 when there is no refresh token or Clerk refused it (the session really ended) and
        503 when Clerk could not be reached (the session is fine, the caller must NOT sign the user out)."""
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
            # the keychain (a prompt can block for as long as the person takes) and the file never run on the loop
            refresh = await asyncio.to_thread(self.store.load_refresh, user_id)
            if not refresh:
                log.warning("no refresh token stored for this session")
                raise ClerkError("errors.session_expired", status=401)
            ts = await self.clerk.refresh(refresh)
            await asyncio.to_thread(self.store.save_refresh, user_id, ts.refresh_token)
            self.store.set_access(user_id, ts.access_token, ts.expires_at)
            return ts.access_token
