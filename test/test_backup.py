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


# --- export to the data folder (the desktop webview cannot save a download) ------------------------
def test_post_export_writes_into_the_data_folder_and_says_where(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    from app.gateways.sqlite.backup import read_meta

    upload_samples(client)
    r = client.post("/api/v1/user/data/export")
    assert r.status_code == 200, r.text
    body = r.json()
    path = services.paths.root / "exports" / body["name"]
    assert body["path"] == str(path) and path.is_file() and body["size_bytes"] == path.stat().st_size
    assert read_meta(path)["format"] == "husk-backup"  # a real, importable backup


def test_exports_in_the_same_second_do_not_collide_and_old_ones_are_pruned(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    # no export replaces a file that is still kept, even within one second (names only repeat once pruned)
    names = [client.post("/api/v1/user/data/export").json()["name"] for _ in range(4)]
    assert len(set(names)) == 4
    names += [client.post("/api/v1/user/data/export").json()["name"] for _ in range(4)]
    kept = sorted(p.name for p in (services.paths.root / "exports").glob("husk-export-*.db"))
    assert len(kept) == 5 and set(kept) <= set(names)
    assert names[-1] in kept  # the newest survives


def test_safety_copies_are_pruned_too(client: TestClient, services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)
    data = _export(client, tmp_path)
    for _ in range(5):
        r = client.post("/api/v1/user/data/import", files={"file": ("b.db", data)}, data={"confirm": "true"})
        assert r.status_code == 200, r.text
    assert len(list((services.paths.root / "exports").glob("pre-import-*.db"))) == 3


def test_reveal_only_opens_backups_this_app_created(client: TestClient, services, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.helpers import filemanager

    opened: list = []
    monkeypatch.setattr(filemanager, "reveal", lambda p: opened.append(p))
    name = client.post("/api/v1/user/data/export").json()["name"]
    assert client.post("/api/v1/user/data/export/reveal", json={"name": name}).status_code == 200
    assert opened == [services.paths.root / "exports" / name]
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
