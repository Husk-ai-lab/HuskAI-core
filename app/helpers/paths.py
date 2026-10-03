"""OS-specific permanent storage.

Matches the technical plan's "Where files go": the shell passes HUSK_DATA_DIR; when Husk
runs on its own we fall back to the platform's application-data folder:

    macOS    ~/Library/Application Support/ai.husk.desktop/data/husk
    Windows  %APPDATA%\\ai.husk.desktop\\data\\husk
    Linux    ~/.local/share/ai.husk.desktop/data/husk
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

from app.helpers.settings import Settings

APP_ID = "ai.husk.desktop"


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "husk.db"

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
        root = Path(user_data_dir(APP_ID, appauthor=False, roaming=True)) / "data" / "husk"
    return DataPaths(root=root).ensure()
