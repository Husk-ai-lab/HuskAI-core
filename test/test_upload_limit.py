"""Hard cap on documents per organisation (50 by default; 3 here to keep the test small)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import config


def _upload(client: TestClient, names: list[str]) -> dict:
    files = [("files", (n, f"# {n}\nTesto diverso {n}.".encode(), "text/markdown")) for n in names]
    r = client.post("/api/v1/documents", files=files, data={"authorized": "true"})
    assert r.status_code == 200, r.text
    return r.json()


def test_default_cap_is_fifty() -> None:
    assert config.MAX_DOCUMENTS_PER_ORG == 50


def test_new_files_beyond_the_cap_are_rejected(client: TestClient, services) -> None:  # type: ignore[no-untyped-def]
    services.settings.max_documents = 3
    r = _upload(client, ["a.md", "b.md", "c.md", "d.md"])  # a folder upload partly over the cap
    assert [d["filename"] for d in r["documents"]] == ["a.md", "b.md", "c.md"]
    assert r["rejected"][0]["filename"] == "d.md" and r["rejected"][0]["code"] == "errors.upload_limit"
    assert "3" in r["rejected"][0]["reason"]
    again = _upload(client, ["a.md"])  # the identical file again: not a new document
    assert again["documents"] and not again["rejected"]
    assert client.get("/api/v1/documents/quota").json() == {"count": 3, "limit": 3}
    client.delete(f"/api/v1/documents/{r['documents'][0]['id']}")
    assert _upload(client, ["d.md"])["documents"]  # room again after a delete
