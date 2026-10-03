"""Single-file backup: export EVERYTHING to one `.db`, and import it back.

Export = `VACUUM INTO` a consistent snapshot of husk.db, plus two extra tables:
  husk_export_meta   format, versions, created_at, org ids
  husk_export_files  every uploaded document file as a BLOB (path relative to data dir)
The BM25 index is not exported; it is rebuilt from the chunks on import (as in the plan).

Import = validate the file, keep a safety copy of the current database, copy the snapshot
into the live database with SQLite's online backup API, restore the document files and
re-run migrations. The caller then rebuilds the search indexes.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.gateways.sqlite.database import Database
from app.helpers.paths import DataPaths
from app.helpers.time import iso, utcnow
from app.i18n.errors import LocalizedError

FORMAT = "husk-backup"
FORMAT_VERSION = "1"
EXPORT_TABLES = ("husk_export_meta", "husk_export_files")


class BackupError(LocalizedError):
    status = 400


@dataclass
class ImportResult:
    org_ids: list[str]
    documents: int
    files: int
    safety_copy: str


def export_backup(db: Database, paths: DataPaths, app_version: str) -> Path:
    stamp = utcnow().strftime("%Y-%m-%d-%H%M%S")
    out_dir = paths.root / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"husk-export-{stamp}.db"
    if out.exists():
        out.unlink()
    with db.read() as c:
        c.execute("VACUUM INTO ?", (str(out),))
    conn = sqlite3.connect(out)
    try:
        conn.execute("CREATE TABLE husk_export_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE husk_export_files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, content BLOB NOT NULL)"
        )
        orgs = [r[0] for r in conn.execute("SELECT id FROM orgs")]
        schema = conn.execute("SELECT MAX(name) FROM schema_migrations").fetchone()[0]
        meta = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": app_version,
            "schema_version": schema or "",
            "created_at": iso(),
            "org_ids": ",".join(orgs),
        }
        conn.executemany("INSERT INTO husk_export_meta(key, value) VALUES (?, ?)", meta.items())
        # Machine-local credentials never travel inside a backup: UI sessions and AI keys.
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM user_ai_keys")
        n = 0
        if paths.documents.exists():
            for f in sorted(paths.documents.rglob("*")):
                if f.is_file():
                    data = f.read_bytes()
                    conn.execute(
                        "INSERT INTO husk_export_files(path, sha256, content) VALUES (?, ?, ?)",
                        (f.relative_to(paths.root).as_posix(), hashlib.sha256(data).hexdigest(), data),
                    )
                    n += 1
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return out


def read_meta(path: Path) -> dict[str, str]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupError("errors.backup_not_database") from exc
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("errors.backup_corrupted")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(EXPORT_TABLES) <= tables:
            raise BackupError("errors.backup_not_husk")
        meta = dict(conn.execute("SELECT key, value FROM husk_export_meta").fetchall())
    except sqlite3.DatabaseError as exc:
        raise BackupError("errors.backup_not_database") from exc
    finally:
        conn.close()
    if meta.get("format") != FORMAT:
        raise BackupError("errors.backup_unknown_format")
    if meta.get("format_version") != FORMAT_VERSION:
        raise BackupError("errors.backup_unsupported_version", version=str(meta.get("format_version")))
    return meta


def import_backup(db: Database, paths: DataPaths, backup_file: Path, require_org: str | None) -> ImportResult:
    meta = read_meta(backup_file)
    org_ids = [o for o in meta.get("org_ids", "").split(",") if o]
    if require_org and require_org not in org_ids:
        raise BackupError("errors.backup_other_org")

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "staged.db"
        shutil.copyfile(backup_file, staged)
        files: list[tuple[str, str, bytes]] = []
        conn = sqlite3.connect(staged)
        try:
            for path, sha, content in conn.execute("SELECT path, sha256, content FROM husk_export_files"):
                if hashlib.sha256(content).hexdigest() != sha:
                    raise BackupError("errors.backup_corrupted_file", path=path)
                rel = Path(path)
                if rel.is_absolute() or ".." in rel.parts or rel.parts[:1] != ("documents",):
                    raise BackupError("errors.backup_invalid_path")
                files.append((path, sha, content))
            for t in EXPORT_TABLES:
                conn.execute(f"DROP TABLE {t}")
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()

        # Safety copy of what we are about to replace.
        safety = paths.root / "exports" / f"pre-import-{utcnow().strftime('%Y-%m-%d-%H%M%S')}.db"
        safety.parent.mkdir(parents=True, exist_ok=True)
        with db.read() as c:
            c.execute("VACUUM INTO ?", (str(safety),))

        src = sqlite3.connect(staged)
        try:
            with db._write_lock:  # noqa: SLF001 - exclusive while the live db is overwritten
                dst = db.connect()
                try:
                    src.backup(dst)
                finally:
                    dst.close()
        finally:
            src.close()

    # Restore document files (replace the documents folder atomically-ish).
    new_docs = paths.root / "documents.importing"
    if new_docs.exists():
        shutil.rmtree(new_docs)
    for path, _sha, content in files:
        target = new_docs / Path(path).relative_to("documents")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    new_docs.mkdir(exist_ok=True)
    old_docs = paths.documents
    trash = paths.root / "documents.old"
    if trash.exists():
        shutil.rmtree(trash)
    if old_docs.exists():
        old_docs.rename(trash)
    new_docs.rename(old_docs)
    shutil.rmtree(trash, ignore_errors=True)
    shutil.rmtree(paths.bm25, ignore_errors=True)
    paths.bm25.mkdir(parents=True, exist_ok=True)

    db.migrate()  # bring an older backup up to the current schema
    return ImportResult(org_ids=org_ids, documents=docs, files=len(files), safety_copy=str(safety))
