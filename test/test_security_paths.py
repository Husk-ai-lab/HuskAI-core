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


def test_logs_scrub_credentials_in_urls_and_headers() -> None:
    """The Telegram bot token is in every Bot API URL; OAuth redirects and bearer tokens must not reach the log file."""
    import logging

    from app.helpers.logging import ScrubFilter, scrub

    token = "123456789:AAH-secret_bot_token_VALUE"
    assert token not in scrub(f"POST https://api.telegram.org/bot{token}/getMe")
    out = scrub("GET /callback?code=abc123&state=xyz789 HTTP/1.1")
    assert "abc123" not in out and "xyz789" not in out and "/callback?code=<redacted>" in out
    assert "eyJ" not in scrub("Authorization: Bearer eyJhbGciOi.eyJzdWIi.sig-nature") and "<jwt>" not in scrub("Bearer abcdefghijkl")
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "request %s to %s", ("A", f"https://api.telegram.org/bot{token}/send"), None)
    assert ScrubFilter().filter(rec) and token not in rec.getMessage()  # placeholders are scrubbed too
