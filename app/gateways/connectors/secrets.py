"""Connector credentials live in the OS keychain (never in SQLite, never in a backup, never in a log).

One JSON blob per organisation and connector. Where no keychain exists (headless Linux, CI) they stay in memory
for the process only: the connector then reads as "not configured" after a restart, which is the safe failure."""

from __future__ import annotations

import json

from app.helpers.logging import get_logger

log = get_logger("connectors")
SERVICE = "ai.husk.desktop.connectors"


class ConnectorSecrets:
    def __init__(self, use_keyring: bool = True):
        self.use_keyring = use_keyring
        self._mem: dict[str, str] = {}

    @staticmethod
    def _key(org_id: str, kind: str) -> str:
        return f"{org_id}:{kind}"

    def get(self, org_id: str, kind: str) -> dict[str, str] | None:
        key = self._key(org_id, kind)
        raw: str | None = None
        if self.use_keyring:
            try:
                import keyring

                raw = keyring.get_password(SERVICE, key)
            except Exception:
                raw = None
        raw = raw or self._mem.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def set(self, org_id: str, kind: str, data: dict[str, str]) -> None:
        key, raw = self._key(org_id, kind), json.dumps(data)
        if self.use_keyring:
            try:
                import keyring

                keyring.set_password(SERVICE, key, raw)
                self._mem.pop(key, None)
                return
            except Exception as exc:
                log.warning("keyring unavailable, keeping the credential in memory: %s", type(exc).__name__)
        self._mem[key] = raw

    def delete(self, org_id: str, kind: str) -> None:
        key = self._key(org_id, kind)
        self._mem.pop(key, None)
        if self.use_keyring:
            try:
                import keyring

                keyring.delete_password(SERVICE, key)
            except Exception:
                pass
