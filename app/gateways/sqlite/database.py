"""SQLite connection management and migrations.

One short-lived connection per unit of work keeps this safe across FastAPI's thread pool
and background tasks. WAL mode lets the scheduler and the UI read while a run writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from app.helpers.time import iso

SCHEMA_VERSION_KEY = "schema_version"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction."""
        with self._write_lock:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    # --- migrations -------------------------------------------------------------------
    def migrate(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        files = sorted(
            (f for f in resources.files("app.gateways.sqlite.migrations").iterdir() if f.name.endswith(".sql")),
            key=lambda f: f.name,
        )
        with self._write_lock:
            conn = self.connect()
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
                applied = 0
                for f in files:
                    if f.name in done:
                        continue
                    conn.executescript("BEGIN;" + f.read_text("utf-8") + ";COMMIT;")
                    conn.execute(
                        "INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)", (f.name, iso())
                    )
                    applied += 1
                return applied
            finally:
                conn.close()

    def schema_version(self) -> str:
        with self.read() as c:
            row = c.execute("SELECT name FROM schema_migrations ORDER BY name DESC LIMIT 1").fetchone()
            return row["name"] if row else "none"

    # --- kv -----------------------------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        with self.read() as c:
            row = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value, default=str), iso()),
            )
