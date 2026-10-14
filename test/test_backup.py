"""Single-file backup: export EVERYTHING to one .db, import it back."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from test.conftest import upload_samples
from test.test_discovery_e2e import wait_run


def _export(client: TestClient, tmp_path) -> bytes:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/user/data/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment; filename=\"husk-export-")
    return r.content


def test_export_contains_everything(client: TestClient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)
    run = client.post("/api/v1/discovery/runs").json()["run"]
    wait_run(client, run["id"])
    data = _export(client, tmp_path)
    f = tmp_path / "b.db"
    f.write_bytes(data)
    con = sqlite3.connect(f)
    meta = dict(con.execute("SELECT key, value FROM husk_export_meta"))
    assert meta["format"] == "husk-backup" and "org_test" in meta["org_ids"]
    assert con.execute("SELECT COUNT(*) FROM husk_export_files").fetchone()[0] == 3  # original files as BLOBs
    for table in ("documents", "chunks", "automations", "facts", "network_notes", "memory_docs", "discovery_runs"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0, table
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0  # credentials never exported
    con.close()


def test_import_roundtrip(client: TestClient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)
    data = _export(client, tmp_path)
    # wipe: delete every document
    for d in client.get("/api/v1/documents").json():
        client.delete(f"/api/v1/documents/{d['id']}")
    assert client.get("/api/v1/documents").json() == []
    r = client.post("/api/v1/user/data/import", files={"file": ("b.db", data)}, data={"confirm": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["documents"] == 3 and r.json()["files"] == 3
    docs = client.get("/api/v1/documents").json()
    assert len(docs) == 3
    # still signed in, files restored, search index rebuilt
    assert client.get("/api/v1/user/me").status_code == 200
    bom = next(d for d in docs if d["kind"] == "xlsx")
    assert client.get(f"/api/v1/documents/{bom['id']}/file").status_code == 200
    assert client.get("/api/v1/documents/search", params={"q": "fornitore"}).json()


def test_import_requires_confirmation_and_valid_file(client: TestClient) -> None:
    r = client.post("/api/v1/user/data/import", files={"file": ("b.db", b"xx")}, data={"confirm": "false"})
    assert r.status_code == 400
    r = client.post("/api/v1/user/data/import", files={"file": ("b.db", b"not a database")}, data={"confirm": "true"})
    assert r.status_code == 400


def test_import_rejects_other_org(client: TestClient, services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    data = _export(client, tmp_path)
    f = tmp_path / "x.db"
    f.write_bytes(data)
    con = sqlite3.connect(f)
    con.execute("UPDATE husk_export_meta SET value = 'org_altra' WHERE key = 'org_ids'")
    con.commit()
    con.close()
    r = client.post("/api/v1/user/data/import", files={"file": ("x.db", f.read_bytes())}, data={"confirm": "true"})
    assert r.status_code == 400 and r.json()["code"] == "errors.backup_other_org"


# --- export to a folder (the desktop webview cannot save a download) --------------------------------
def test_post_export_writes_a_backup_outside_the_wiped_folders_and_says_where(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    from app.gateways.sqlite.backup import read_meta

    upload_samples(client)
    r = client.post("/api/v1/user/data/export")
    assert r.status_code == 200, r.text
    body = r.json()
    path = services.paths.backups / body["name"]
    assert body["path"] == str(path) and body["folder"] == str(services.paths.backups) and path.is_file()
    assert body["in_data_folder"] is False and body["size_bytes"] == path.stat().st_size
    assert read_meta(path)["format"] == "husk-backup"  # a real, importable backup


def test_exports_in_the_same_second_do_not_collide_and_the_users_folder_is_never_pruned(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    names = [client.post("/api/v1/user/data/export").json()["name"] for _ in range(8)]
    assert len(set(names)) == 8  # nothing replaced anything else
    assert len(list(services.paths.backups.glob("husk-export-*.db"))) == 8  # and nothing was deleted


def test_when_the_backup_folder_cannot_be_written_it_falls_back_and_says_so(settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient as TC

    from app.endpoints.deps import SESSION_COOKIE
    from app.main import build_services, create_app
    from test.conftest import login

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")  # a file where the backup folder should be: mkdir fails
    st = settings.model_copy(update={"backup_dir": blocker / "Husk backups"})
    svc = build_services(st)
    with TC(create_app(st, svc), base_url="http://127.0.0.1:8765") as c:
        c.cookies.set(SESSION_COOKIE, login(svc))
        r = c.post("/api/v1/user/data/export").json()
    assert r["in_data_folder"] is True and r["folder"] == str(svc.paths.exports)
    assert (svc.paths.exports / r["name"]).is_file()


def test_a_failed_export_leaves_no_file(client: TestClient, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.gateways.sqlite import backup

    real = backup._write_export

    def broken(db, paths, version, out):  # type: ignore[no-untyped-def]
        real(db, paths, version, out)  # the file exists ...
        raise RuntimeError("disk full")  # ... then the export fails

    monkeypatch.setattr(backup, "_write_export", broken)
    assert client.post("/api/v1/user/data/export").status_code == 500
    assert not list(services.paths.backups.glob("*.db"))


def test_safety_copies_are_pruned(client: TestClient, services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)
    data = _export(client, tmp_path)
    for _ in range(5):
        r = client.post("/api/v1/user/data/import", files={"file": ("b.db", data)}, data={"confirm": "true"})
        assert r.status_code == 200, r.text
    assert len(list(services.paths.exports.glob("pre-import-*.db"))) == 3


def test_reveal_only_opens_backups_this_app_created(client: TestClient, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.helpers import filemanager

    opened: list = []
    monkeypatch.setattr(filemanager, "reveal", lambda p: opened.append(p))
    name = client.post("/api/v1/user/data/export").json()["name"]
    assert client.post("/api/v1/user/data/export/reveal", json={"name": name}).status_code == 200
    assert opened == [services.paths.backups / name]
    for bad in ("../husk.db", "/etc/passwd", "husk.db", "husk-export-2026-01-01-000000.db/../../x", "..\\x.db"):
        assert client.post("/api/v1/user/data/export/reveal", json={"name": bad}).status_code == 400, bad
    missing = client.post("/api/v1/user/data/export/reveal", json={"name": "husk-export-2020-01-01-000000.db"})
    assert missing.status_code == 404 and len(opened) == 1


def test_reveal_command_never_goes_through_a_shell() -> None:
    from pathlib import Path

    from app.helpers.filemanager import reveal_command

    p = Path("/data/exports/husk-export-2026-10-14-124944.db")
    assert reveal_command(p, "darwin") == ["open", "-R", str(p)]
    assert reveal_command(p, "win32") == ["explorer", f"/select,{p}"]
    assert reveal_command(p, "linux") == ["xdg-open", str(p.parent)]


def test_delete_all_data_keeps_exported_backups_and_says_where(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    name = client.post("/api/v1/user/data/export").json()["name"]
    r = client.request("DELETE", "/api/v1/user/data", json={"confirm": True})
    assert r.status_code == 200 and r.json()["backups_folder"] == str(services.paths.backups)
    assert (services.paths.backups / name).is_file()


def test_the_page_shell_is_revalidated_and_hashed_assets_are_immutable(client: TestClient) -> None:
    from app.main import FRONTEND_DIST

    if not (FRONTEND_DIST / "index.html").exists():
        import pytest

        pytest.skip("UI not built")
    assert client.get("/").headers["cache-control"] == "no-cache"
    asset = next((FRONTEND_DIST / "assets").glob("*.js"))
    assert "immutable" in client.get(f"/assets/{asset.name}").headers["cache-control"]
