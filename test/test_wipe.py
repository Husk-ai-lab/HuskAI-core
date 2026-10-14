"""Delete all local data: only what Husk created goes, the user stays signed in with their settings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.gateways.sqlite.repositories import AutomationRepo
from app.gateways.sqlite.wipe import WipeError, wipe_data
from app.helpers.paths import DataPaths
from test.conftest import ORG, upload_samples
from test.test_discovery_e2e import wait_run


def _populate(client: TestClient) -> None:
    upload_samples(client)
    wait_run(client, client.post("/api/v1/discovery/runs").json()["run"]["id"])
    client.get("/api/v1/user/data/export")  # leaves a backup in the backup folder (kept by a wipe)


def test_wipe_empties_the_folder_and_keeps_the_user_signed_in(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    root = services.paths.root
    _populate(client)
    client.patch("/api/v1/user/settings", json={"language": "it"})
    assert client.get("/api/v1/documents").json() and AutomationRepo(services.db, ORG).list()
    (root / "exports").mkdir(exist_ok=True)
    (root / "exports" / "pre-import-2026-01-01-000000.db").write_bytes(b"x")  # a safety copy: part of the data
    assert any((root / "documents").rglob("*.*")) and any((root / "exports").iterdir())
    kept = sorted(p.name for p in services.paths.backups.glob("husk-export-*.db"))
    assert kept

    r = client.request("DELETE", "/api/v1/user/data", json={"confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["documents"] == 3 and body["automations"] >= 1 and body["bytes_freed"] > 0

    assert client.get("/api/v1/documents").json() == []
    assert client.get("/api/v1/automations").json() == []
    assert not any((root / "documents").rglob("*.*")) and not (root / "exports").exists()
    assert sorted(p.name for p in services.paths.backups.glob("husk-export-*.db")) == kept  # exported backups stay
    me = client.get("/api/v1/user/me")
    assert me.status_code == 200 and me.json()["settings"]["language"] == "it"  # still signed in, settings kept
    assert client.get("/api/v1/documents/search", params={"q": "fornitore"}).json() == []
    # the database is a fresh, migrated one: new data works straight away
    upload_samples(client)
    assert len(client.get("/api/v1/documents").json()) == 3
    # and a second wipe on an already-empty folder is fine
    assert client.request("DELETE", "/api/v1/user/data", json={"confirm": True}).status_code == 200


def test_wipe_requires_confirmation_and_login(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)
    assert client.request("DELETE", "/api/v1/user/data", json={"confirm": False}).status_code == 400
    assert client.request("DELETE", "/api/v1/user/data", json={}).status_code == 400
    assert len(client.get("/api/v1/documents").json()) == 3  # nothing was touched
    client.cookies.clear()
    assert client.request("DELETE", "/api/v1/user/data", json={"confirm": True}).status_code == 401


def test_wipe_waits_for_a_running_discovery(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    upload_samples(client)

    # a task that is not done = a discovery in flight
    class Pending:
        def done(self) -> bool:
            return False

    services.tasks["discovery:x"] = Pending()  # type: ignore[assignment]
    r = client.request("DELETE", "/api/v1/user/data", json={"confirm": True})
    assert r.status_code == 409 and r.json()["code"] == "errors.wipe_discovery_running"
    assert len(client.get("/api/v1/documents").json()) == 3
    del services.tasks["discovery:x"]


def test_only_husk_files_are_removed_never_the_root(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    """HUSK_DATA_DIR / --data-dir may be a folder that also holds other things."""
    root = services.paths.root
    (root / "my-notes.txt").write_text("keep me")
    (root / "photos").mkdir()
    (root / "photos" / "a.jpg").write_bytes(b"x")
    upload_samples(client)
    assert client.request("DELETE", "/api/v1/user/data", json={"confirm": True}).status_code == 200
    assert (root / "my-notes.txt").read_text() == "keep me" and (root / "photos" / "a.jpg").exists()
    assert root.exists() and (root / "husk.db").exists()


def test_log_files_are_emptied_not_unlinked(services) -> None:  # type: ignore[no-untyped-def]
    log = services.paths.logs / "husk.log"
    log.write_text("line\n" * 50)
    wipe_data(services.db, services.paths)
    assert log.exists() and log.read_text() == ""


@pytest.mark.parametrize("unsafe", ["/", "home", "home-parent"])
def test_unsafe_roots_are_refused(services, tmp_path: Path, unsafe: str) -> None:  # type: ignore[no-untyped-def]
    root = {"/": Path("/"), "home": Path.home(), "home-parent": Path.home().parent}[unsafe]
    with pytest.raises(WipeError):
        wipe_data(services.db, DataPaths(root=root))
    assert sqlite3.connect(services.paths.db).execute("SELECT COUNT(*) FROM orgs").fetchone() is not None  # untouched


def test_wipe_removes_the_connector_secrets_file(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    """With the file store the credentials are in the data folder: deleting all data deletes them too."""
    from app.gateways.connectors.secrets import ConnectorSecrets

    store = ConnectorSecrets(file=services.paths.connector_secrets)
    store.set(ORG, "gmail", {"address": "a@example.com", "password": "abcdefghijklmnop", "hint": "a@example.com"})
    assert services.paths.connector_secrets.exists()
    assert client.request("DELETE", "/api/v1/user/data", json={"confirm": True}).status_code == 200
    assert not services.paths.connector_secrets.exists()
    assert store.get(ORG, "gmail") is None
