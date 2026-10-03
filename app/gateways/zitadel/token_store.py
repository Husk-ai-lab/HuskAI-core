"""Refresh tokens live in the OS keychain (never in a plain file); access tokens in memory."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.helpers.logging import get_logger

log = get_logger("tokens")
SERVICE = "ai.husk.desktop"


class TokenStore:
    def __init__(self, use_keyring: bool = True):
        self.use_keyring = use_keyring
        self._mem_refresh: dict[str, str] = {}
        self._access: dict[str, _Access] = {}
        self._id_tokens: dict[str, str] = {}

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
        self._id_tokens.pop(user_id, None)
        if self.use_keyring:
            try:
                import keyring

                keyring.delete_password(SERVICE, user_id)
            except Exception:
                pass

    def set_access(self, user_id: str, token: str, expires_at: float, id_token: str | None = None) -> None:
        self._access[user_id] = _Access(token, expires_at)
        if id_token:
            self._id_tokens[user_id] = id_token

    def get_access(self, user_id: str) -> str | None:
        a = self._access.get(user_id)
        if a and a.expires_at - 60 > time.time():
            return a.token
        return None

    def id_token(self, user_id: str) -> str | None:
        return self._id_tokens.get(user_id)


@dataclass
class _Access:
    token: str
    expires_at: float
