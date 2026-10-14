"""A small JSON object of strings in an owner-only file, replaced atomically.

Shared by the connector credentials and the sign-in refresh token (file store). The file is plain text: it is as
protected as the data folder (permissions 0600 from the first byte; on Windows the per-user AppData ACL).
Nothing here logs a value."""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path

from app.helpers.logging import get_logger

log = get_logger("private_json")


class PrivateJsonFile:
    def __init__(self, path: Path, label: str = "secrets"):
        self.path, self.label = path, label
        self.lock = threading.RLock()  # callers hold it around read-modify-write

    def read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("%s file unreadable, treating it as empty: %s", self.label, type(exc).__name__)
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only from the first byte
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        os.replace(tmp, self.path)
