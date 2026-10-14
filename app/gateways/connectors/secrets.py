"""Connector credentials: a private JSON file in the data folder (default) or the OS keychain.

One JSON blob per organisation and connector, never in SQLite, never in a backup, never in a log.

* `file` (default, `HUSK_SECRET_STORE=file`): `connector-secrets.json` in the data folder, owner-only (0600), written
  atomically. This exists because a macOS keychain item is bound to the exact binary that created it: an unsigned
  build asks for the login password again after every rebuild or update. The file is plain text: it is as protected
  as the data folder itself (disk encryption, file permissions). It is removed by "Delete all data".
* `keychain` (`HUSK_SECRET_STORE=keychain`): the OS keychain. Where no keychain exists (headless Linux, CI) the secrets
  stay in memory for the process only: the connector then reads as "not configured" after a restart, the safe failure.

The file store never touches the keychain, so it never causes a keychain prompt."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from app.helpers.logging import get_logger
from app.helpers.private_json import PrivateJsonFile

if TYPE_CHECKING:
    from app.helpers.paths import DataPaths
    from app.helpers.settings import Settings

log = get_logger("connectors")
SERVICE = "ai.husk.desktop.connectors"


class ConnectorSecrets:
    def __init__(self, use_keyring: bool = True, file: Path | None = None):
        self.use_keyring = use_keyring and file is None
        self.file = file
        self._store = PrivateJsonFile(file, "connector secrets") if file is not None else None
        self._mem: dict[str, str] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, paths: DataPaths) -> ConnectorSecrets:
        """Tests (`env="test"`) keep secrets in memory, whatever the setting says."""
        if settings.env == "test":
            return cls(use_keyring=False)
        if settings.secret_store == "keychain":
            return cls(use_keyring=True)
        return cls(file=paths.connector_secrets)

    @staticmethod
    def _key(org_id: str, kind: str) -> str:
        return f"{org_id}:{kind}"

    # --- file store -----------------------------------------------------------------------------------------
    def _read_file(self) -> dict[str, str]:
        assert self._store is not None
        return self._store.read()

    def _write_file(self, data: dict[str, str]) -> None:
        assert self._store is not None
        self._store.write(data)

    # --- public API -----------------------------------------------------------------------------------------
    def get(self, org_id: str, kind: str) -> dict[str, str] | None:
        key = self._key(org_id, kind)
        raw: str | None = None
        if self.file is not None:
            with self._lock:
                raw = self._read_file().get(key)
        elif self.use_keyring:
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
        if self.file is not None:
            try:
                with self._lock:
                    stored = self._read_file()
                    stored[key] = raw
                    self._write_file(stored)
                self._mem.pop(key, None)
                return
            except OSError as exc:
                log.warning("connector secrets file not writable, keeping the credential in memory: %s", type(exc).__name__)
        elif self.use_keyring:
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
        if self.file is not None:
            try:
                with self._lock:
                    stored = self._read_file()
                    if stored.pop(key, None) is not None:
                        self._write_file(stored)
            except OSError as exc:
                log.warning("connector secrets file not writable: %s", type(exc).__name__)
        elif self.use_keyring:
            try:
                import keyring

                keyring.delete_password(SERVICE, key)
            except Exception:
                pass
