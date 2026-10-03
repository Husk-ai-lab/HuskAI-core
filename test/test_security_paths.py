"""Loopback hardening and OS-specific data folder."""

from __future__ import annotations

from pathlib import Path

from app.helpers.paths import resolve_data_paths
from app.helpers.settings import Settings


def test_foreign_origin_cannot_write(client) -> None:  # type: ignore[no-untyped-def]
    r = client.patch("/api/v1/user/settings", json={"theme": "dark"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.patch("/api/v1/user/settings", json={"theme": "dark"}, headers={"Origin": "http://127.0.0.1:8765"})
    assert ok.status_code == 200


def test_internal_requires_launch_secret(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/internal/studio-token").status_code == 403


def test_data_dir_env_override(tmp_path: Path) -> None:
    p = resolve_data_paths(Settings(HUSK_ENV="test", HUSK_DATA_DIR=tmp_path / "x"))
    assert p.root == tmp_path / "x" and p.db.name == "husk.db" and p.bm25.exists()


def test_default_data_dir_is_os_app_data(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import app.helpers.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda *a, **k: str(tmp_path / "AppSupport" / "ai.husk.desktop"))
    p = resolve_data_paths(Settings(HUSK_ENV="test", HUSK_DATA_DIR=None))
    assert p.root == tmp_path / "AppSupport" / "ai.husk.desktop" / "data" / "husk"


def test_logs_scrub_personal_data() -> None:
    from app.helpers.logging import scrub

    s = scrub("mail mario.rossi@azienda.it tel +39 333 1234567 cf RSSMRA80A01H501U")
    assert "@" not in s and "333" not in s and "RSSMRA" not in s
