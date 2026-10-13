"""Delete ALL local data (the data folder) and start again from an empty database.

Only the folders and files Husk itself creates are removed (`husk.db` and its WAL files, `documents/`, `bm25/`,
`runs/`, `outputs/`, `exports/` with the old backups, and `connector-secrets.json` when the file store is used). The data root is NEVER removed as a whole: `HUSK_DATA_DIR` /
`--data-dir` may point at a folder that holds other things. Log files are emptied, not unlinked (the logger keeps
the active file open). Keychain items (login, and connector secrets when `HUSK_SECRET_STORE=keychain`) are not touched: they are not in the folder.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.gateways.sqlite.database import Database
from app.helpers.logging import get_logger
from app.helpers.paths import DataPaths
from app.i18n.errors import LocalizedError

log = get_logger("wipe")
_DB_FILES = ("husk.db", "husk.db-wal", "husk.db-shm", "connector-secrets.json", "connector-secrets.json.tmp")
_FOLDERS = ("documents", "bm25", "runs", "outputs", "exports")


class WipeError(LocalizedError):
    status = 400


@dataclass
class WipeResult:
    documents: int
    automations: int
    bytes_freed: int


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _check_root(root: Path) -> None:
    resolved, home = root.resolve(), Path.home().resolve()
    if resolved == Path(resolved.anchor) or resolved == home or resolved in home.parents:
        raise WipeError("errors.wipe_unsafe_root")


def _count(db: Database, table: str) -> int:
    with db.read() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 - fixed table names


def wipe_data(db: Database, paths: DataPaths) -> WipeResult:
    """Empty the data folder and recreate a fresh, migrated database. The caller stops background work first."""
    _check_root(paths.root)
    result = WipeResult(documents=_count(db, "documents"), automations=_count(db, "automations"), bytes_freed=0)
    with db._write_lock:  # noqa: SLF001 - nobody writes while the file goes away
        for name in _DB_FILES:
            f = paths.root / name
            if f.exists():
                result.bytes_freed += _size(f)
                f.unlink()
        for name in _FOLDERS:
            folder = paths.root / name
            if folder.exists():
                result.bytes_freed += _size(folder)
                shutil.rmtree(folder)
        if paths.logs.exists():
            for f in paths.logs.iterdir():
                if f.is_file():
                    result.bytes_freed += _size(f)
                    f.write_text("")
        paths.ensure()
        db.migrate()
    log.info("local data deleted: %s documents, %s automations, %s bytes", result.documents, result.automations, result.bytes_freed)
    return result
