"""OS-specific permanent storage.

Matches the technical plan's "Where files go": the shell passes HUSK_DATA_DIR; when Husk
runs on its own we fall back to the platform's application-data folder:

    macOS    ~/Library/Application Support/ai.husk.desktop/data/husk
    Windows  %APPDATA%\\ai.husk.desktop\\data\\husk
    Linux    ~/.local/share/ai.husk.desktop/data/husk

On a server (see `helpers/runtime.py`) this fallback is never used: the folder must be given explicitly
(`python -m app.main --data-dir PATH`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir, user_documents_dir

from app.helpers.runtime import detect_environment
from app.helpers.settings import Settings

APP_ID = "ai.husk.desktop"


class DataDirRequired(RuntimeError):
    """Husk runs on a server and was not told where its data goes."""


@dataclass(frozen=True)
class DataPaths:
    root: Path
    backups_root: Path | None = None  # where exported backups go; None = inside the data folder (`exports/`)

    @property
    def db(self) -> Path:
        return self.root / "husk.db"

    @property
    def exports(self) -> Path:
        """Inside the data folder: safety copies made before an import, and the fallback for backups. Removed by
        Delete all data."""
        return self.root / "exports"

    @property
    def backups(self) -> Path:
        """Where "Export a backup" writes. Outside the data folder by default, so Delete all data keeps it."""
        return self.backups_root or self.exports

    @property
    def connector_secrets(self) -> Path:
        """Connector credentials (file store): owner-only, never exported in a backup, removed by Delete all data."""
        return self.root / "connector-secrets.json"

    @property
    def session_secrets(self) -> Path:
        """The sign-in refresh token (file store): owner-only, never exported in a backup. NOT removed by Delete all
        data (the user stays signed in), removed on sign-out."""
        return self.root / "session-secrets.json"

    @property
    def bm25(self) -> Path:
        return self.root / "bm25"

    @property
    def documents(self) -> Path:
        return self.root / "documents"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def org_documents(self, org_id: str) -> Path:
        return self.documents / _safe(org_id)

    def org_bm25(self, org_id: str) -> Path:
        return self.bm25 / _safe(org_id)

    def org_outputs(self, org_id: str) -> Path:
        return self.outputs / _safe(org_id)

    def ensure(self) -> DataPaths:
        for p in (self.root, self.bm25, self.documents, self.runs, self.outputs, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self


def _safe(segment: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in segment)


def resolve_data_paths(settings: Settings) -> DataPaths:
    if settings.data_dir:
        root = Path(settings.data_dir).expanduser()
    else:
        info = detect_environment()
        if info.is_server and settings.env != "test":  # defence in depth: `main.run()` already refuses to start
            raise DataDirRequired(f"server detected ({', '.join(info.reasons)}): pass --data-dir PATH")
        root = Path(user_data_dir(APP_ID, appauthor=False, roaming=True)) / "data" / "husk"
    return DataPaths(root=root, backups_root=_backups_root(settings, root)).ensure()


def _backups_root(settings: Settings, root: Path) -> Path:
    """`HUSK_BACKUP_DIR`, else `<Documents>/Husk backups` on a desktop. Tests and servers keep backups beside the
    data (never in a person's Documents). The folder is created when the first backup is written, not here."""
    if settings.backup_dir:
        return Path(settings.backup_dir).expanduser()
    if settings.env == "test" or detect_environment().is_server:
        return root / "backups"
    try:
        return Path(user_documents_dir()) / "Husk backups"
    except Exception:  # no Documents folder known: stay inside the data folder
        return root / "backups"
