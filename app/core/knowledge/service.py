"""Knowledge base service: upload → convert → chunk → index; search; document access."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.knowledge.chunker import chunk_markdown
from app.core.knowledge.index import KnowledgeIndex, to_hit
from app.core.knowledge.ingest import MAX_UPLOAD_BYTES, convert, kind_for, title_for
from app.core.knowledge.tabular.tools import TabularTools
from app.data.models.document import (
    Chunk,
    Document,
    DocumentSearchResult,
    DocumentStatus,
    SearchHit,
)
from app.gateways.sqlite import Database
from app.gateways.sqlite.repositories import DocumentRepo
from app.helpers.ids import new_id
from app.helpers.logging import get_logger
from app.helpers.paths import DataPaths
from app.helpers.time import utcnow
from app.i18n.errors import LocalizedError

log = get_logger("knowledge")


class UploadRejected(LocalizedError):
    status = 400


class KnowledgeService:
    def __init__(self, db: Database, paths: DataPaths, index: KnowledgeIndex, org_id: str):
        self.db, self.paths, self.index, self.org_id = db, paths, index, org_id
        self.docs = DocumentRepo(db, org_id)

    # --- files -------------------------------------------------------------------------
    def file_path(self, doc: Document) -> Path:
        return self.paths.org_documents(self.org_id) / f"{doc.id}{Path(doc.filename).suffix.lower()}"

    def resolve(self, document_id: str) -> tuple[Document, Path] | None:
        doc = self.docs.get(document_id)
        return (doc, self.file_path(doc)) if doc else None

    def tabular(self) -> TabularTools:
        return TabularTools(self.resolve)

    # --- upload ------------------------------------------------------------------------
    def add(self, filename: str, content: bytes, user_id: str, authorized: bool, lang: str = "en",
            source_path: str | None = None) -> Document:
        if not authorized:
            raise UploadRejected("errors.upload_not_authorized")
        if len(content) > MAX_UPLOAD_BYTES:
            raise UploadRejected("errors.upload_too_large", max_mb=60)
        if not content:
            raise UploadRejected("errors.upload_empty")
        filename = Path(filename).name
        sha = hashlib.sha256(content).hexdigest()
        existing = self.docs.by_sha(sha)
        if existing:
            return existing
        now = utcnow()
        doc = Document(
            id=new_id("doc"),
            org_id=self.org_id,
            filename=filename,
            title=title_for(filename),
            kind=kind_for(filename),
            size_bytes=len(content),
            sha256=sha,
            status=DocumentStatus.pending,
            authorized_at=now,
            uploaded_by=user_id,
            source_path=_clean_rel_path(source_path),
            created_at=now,
        )
        path = self.file_path(doc)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.docs.insert(doc)
        self.ingest(doc, lang)
        self.reindex()
        return self.docs.get(doc.id) or doc

    def ingest(self, doc: Document, lang: str = "en") -> None:
        try:
            conv = convert(self.file_path(doc), doc.kind, self.org_id, doc.id, lang)
            raw = chunk_markdown(conv.markdown)
            chunks = [
                Chunk(
                    id=f"{doc.id}#c{i}",
                    org_id=self.org_id,
                    document_id=doc.id,
                    ordinal=i,
                    heading=r.heading,
                    text=r.text,
                    start=r.start,
                    end=r.end,
                )
                for i, r in enumerate(raw)
            ]
            self.docs.replace_chunks(doc.id, chunks)
            self.docs.save_profiles(doc.id, conv.profiles)
            self.docs.mark_ingested(doc.id, conv.markdown, conv.excerpt, conv.rows, None)
        except Exception as exc:
            log.warning("ingest failed doc=%s err=%s", doc.id, type(exc).__name__)
            self.docs.mark_failed(doc.id, f"{type(exc).__name__}: {exc}")

    def delete(self, document_id: str) -> None:
        doc = self.docs.get(document_id)
        if not doc:
            return
        self.file_path(doc).unlink(missing_ok=True)
        self.docs.delete(document_id)
        self.reindex()

    def reindex(self) -> int:
        return self.index.rebuild(self.org_id, self.docs.all_chunks())

    def ensure_index(self) -> None:
        if not self.index.is_built(self.org_id):
            self.reindex()

    # --- search ------------------------------------------------------------------------
    def search_chunks(self, query: str, k: int = 8, document_ids: list[str] | None = None) -> list[SearchHit]:
        self.ensure_index()
        raw = self.index.query(self.org_id, query, k=k * 4 if document_ids else k)
        hits = []
        for chunk_id, score in raw:
            ch = self.docs.chunk(chunk_id)
            if not ch or (document_ids and ch.document_id not in document_ids):
                continue
            hits.append(to_hit(ch, score, query))
            if len(hits) >= k:
                break
        return hits

    def search_documents(self, query: str) -> list[DocumentSearchResult]:
        """Full-text search for the Conoscenza screen: titles, departments and content."""
        q = query.strip()
        if not q:
            return []
        hits = self.search_chunks(q, k=50)
        by_doc: dict[str, list[SearchHit]] = {}
        for h in hits:
            by_doc.setdefault(h.document_id, []).append(h)
        ql = q.lower()
        for d in self.docs.list():
            meta = f"{d.title} {d.filename} {d.department or ''}".lower()
            if ql in meta and d.id not in by_doc:
                by_doc[d.id] = []
        out = []
        for doc_id, doc_hits in by_doc.items():
            doc = self.docs.get(doc_id)
            if not doc:
                continue
            best = doc_hits[0] if doc_hits else SearchHit(
                document_id=doc.id, chunk_id="", score=0.0, text=doc.excerpt or "", highlights=[]
            )
            if not doc_hits and doc.excerpt:
                from app.core.knowledge.index import highlight

                best.highlights = highlight(doc.excerpt, q)
            out.append(DocumentSearchResult(document=doc, matches=max(len(doc_hits), 1), best=best))
        return sorted(out, key=lambda r: -r.best.score)


def _clean_rel_path(path: str | None) -> str | None:
    """Folder uploads: keep a display-only relative path (no absolute paths, no '..')."""
    if not path:
        return None
    parts = [p for p in path.replace("\\", "/").split("/") if p and p not in (".", "..")]
    return "/".join(parts)[:400] or None
