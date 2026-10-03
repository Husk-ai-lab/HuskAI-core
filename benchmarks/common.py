"""Shared benchmark plumbing: an isolated Husk instance on a temp data dir."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.services import Services  # noqa: E402
from app.data.models.user import User  # noqa: E402
from app.gateways.sqlite.repositories import UserRepo  # noqa: E402
from app.helpers.settings import Settings  # noqa: E402
from app.main import build_services  # noqa: E402

RESULTS = Path(__file__).parent / "results"
ORG, USER = "org_bench", "user_bench"


def isolated_services(live: bool) -> Services:
    data = Path(tempfile.mkdtemp(prefix="husk-bench-"))
    overrides = {"HUSK_ENV": "test", "HUSK_DATA_DIR": data, "NEON_DATA_API_URL": None,
                 "HUSK_STUDIO_UI_URL": "http://127.0.0.1:9"}
    if not live:
        overrides["REGOLO_API_KEY"] = None
    svc = build_services(Settings(**overrides))
    if not live:
        # Offline: the scripted model exercises the pipeline deterministically (no AI quality claims).
        from test.fakes import FakeProvider

        svc.llm = FakeProvider()  # type: ignore[assignment]
    UserRepo(svc.db).upsert(User(id=USER, org_id=ORG, org_name="Officine Esempio (fittizia)"))
    return svc


def load_dataset(svc: Services, folder: Path) -> list:
    ks = svc.knowledge(ORG)
    docs = []
    for f in sorted(folder.iterdir()):
        if f.name == "ground_truth.json" or f.is_dir():
            continue
        docs.append(ks.add(f.name, f.read_bytes(), USER, True))
    return docs


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)
