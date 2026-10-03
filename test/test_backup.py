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
