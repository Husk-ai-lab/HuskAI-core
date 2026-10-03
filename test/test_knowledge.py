"""Document pipeline: conversion, chunking, BM25s search with highlights, data tools."""

from __future__ import annotations

from app.core.knowledge.chunker import chunk_markdown
from app.core.knowledge.text_analysis import tokens
from test.conftest import ORG, PROCEDURE_MD, bom_xlsx, login, production_csv


def test_tokens_keep_codes_and_stem_italian() -> None:
    t = tokens("Il fornitore PX200 ricopia i fornitori")
    assert "px200" in t
    assert t.count(tokens("fornitore")[0]) == 2  # fornitore / fornitori share a stem


def test_chunker_keeps_table_header() -> None:
    table = "| A | B |\n|---|---|\n" + "\n".join(f"| riga{i} | {'x' * 60} |" for i in range(80))
    chunks = chunk_markdown("# Foglio\n\n" + table)
    assert len(chunks) > 1
    assert all(c.text.startswith("| A | B |") for c in chunks)
    assert all(c.heading == "Foglio" for c in chunks)


def test_upload_requires_authorisation(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/documents", files=[("files", ("a.md", b"ciao", "text/markdown"))], data={"authorized": "false"})
    assert r.status_code == 400


def test_search_and_preview(client) -> None:  # type: ignore[no-untyped-def]
    from test.conftest import upload_samples

    docs = upload_samples(client)
    res = client.get("/api/v1/documents/search", params={"q": "fornitore"}).json()
    assert res, "search should find the BOM and the procedure"
    hit = res[0]["best"]
    assert hit["highlights"]
    h = hit["highlights"][0]
    assert hit["text"][h["start"] : h["end"]].lower().startswith("fornitor")
    bom = next(d for d in docs if d["kind"] == "xlsx")
    detail = client.get(f"/api/v1/documents/{bom['id']}").json()
    assert detail["document"]["rows"] == 4
    assert detail["profiles"][0]["columns"][3]["name"] == "Fornitore"


def test_duplicate_upload_is_deduplicated(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    ks = services.knowledge(ORG)
    a = ks.add("p.md", PROCEDURE_MD.encode(), "user_test", True)
    b = ks.add("copia.md", PROCEDURE_MD.encode(), "user_test", True)
    assert a.id == b.id and len(ks.docs.list()) == 1


def test_tabular_tools(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    ks = services.knowledge(ORG)
    c = ks.add("Distinta_RevC.xlsx", bom_xlsx("C"), "user_test", True)
    b = ks.add("Distinta_RevB.xlsx", bom_xlsx("B"), "user_test", True)
    p = ks.add("Produzione.csv", production_csv(), "user_test", True)
    t = ks.tabular()
    assert t.missing_values(c.id)["missing_by_column"] == {"Fornitore": 2, "Peso unitario": 1}
    diff = t.compare_versions(b.id, c.id, "Codice")
    assert diff["changed_count"] == 1 and diff["rows"][0]["changes"]["Quantità"] == {"before": 12, "after": 16}
    per = t.detect_periodicity(p.id)
    assert per["cadence"] == "daily" and per["date_column"] == "Data"
    agg = t.aggregate(p.id, "Scarti", "sum")
    assert agg["sum"] == sum(d % 5 for d in range(1, 15))
    assert t.filter_rows(c.id, "Fornitore", "isnull")["matches"] == 2


def test_sqlite_db_document(services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import sqlite3

    login(services)
    dbf = tmp_path / "ordini.db"
    con = sqlite3.connect(dbf)
    con.execute("CREATE TABLE ordini (numero TEXT, cliente TEXT, importo REAL)")
    con.executemany("INSERT INTO ordini VALUES (?, ?, ?)", [("O1", "Cliente A", 100.0), ("O2", "Cliente B", 250.5)])
    con.commit()
    con.close()
    ks = services.knowledge(ORG)
    d = ks.add("ordini.db", dbf.read_bytes(), "user_test", True)
    assert d.status.value == "ingested" and d.rows == 2
    assert ks.tabular().aggregate(d.id, "importo", "sum", sheet="ordini")["sum"] == 350.5
