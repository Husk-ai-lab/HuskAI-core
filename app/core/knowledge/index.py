"""Local BM25 index per organisation (BM25s), saved as files next to the data."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import bm25s
import numpy as np

from app.core.knowledge.text_analysis import token_spans, tokens
from app.data.models.document import Chunk, Highlight, SearchHit
from app.helpers.logging import get_logger

log = get_logger("bm25")


@dataclass
class _Loaded:
    retriever: bm25s.BM25 | None
    chunk_ids: list[str]


class KnowledgeIndex:
    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, _Loaded] = {}
        self._lock = threading.RLock()

    def _dir(self, org_id: str) -> Path:
        from app.helpers.paths import _safe

        return self.root / _safe(org_id)

    def rebuild(self, org_id: str, chunks: list[Chunk]) -> int:
        """Full rebuild: corpora are small (hundreds to a few thousand chunks)."""
        with self._lock:
            d = self._dir(org_id)
            d.mkdir(parents=True, exist_ok=True)
            corpus = [tokens((c.heading or "") + "\n" + c.text) or ["_vuoto_"] for c in chunks]
            ids = [c.id for c in chunks]
            retriever = None
            if corpus:
                retriever = bm25s.BM25()
                retriever.index(corpus, show_progress=False)
                retriever.save(str(d / "index"))
            (d / "chunk_ids.json").write_text(json.dumps(ids))
            self._cache[org_id] = _Loaded(retriever, ids)
            return len(ids)

    def _load(self, org_id: str) -> _Loaded:
        with self._lock:
            if org_id in self._cache:
                return self._cache[org_id]
            d = self._dir(org_id)
            ids_file = d / "chunk_ids.json"
            if not ids_file.exists():
                loaded = _Loaded(None, [])
            else:
                ids = json.loads(ids_file.read_text())
                retriever = bm25s.BM25.load(str(d / "index")) if ids and (d / "index").exists() else None
                loaded = _Loaded(retriever, ids)
            self._cache[org_id] = loaded
            return loaded

    def is_built(self, org_id: str) -> bool:
        return (self._dir(org_id) / "chunk_ids.json").exists()

    def drop(self, org_id: str | None = None) -> None:
        with self._lock:
            if org_id is None:
                self._cache.clear()
            else:
                self._cache.pop(org_id, None)

    def query(self, org_id: str, query: str, k: int = 8) -> list[tuple[str, float]]:
        loaded = self._load(org_id)
        q = tokens(query)
        if not loaded.retriever or not q or not loaded.chunk_ids:
            return []
        k = min(k, len(loaded.chunk_ids))
        try:
            results, scores = loaded.retriever.retrieve([q], k=k, show_progress=False)
        except Exception as exc:  # unknown vocabulary etc.
            log.debug("bm25 retrieve failed: %s", exc)
            return []
        out = []
        for idx, score in zip(np.asarray(results[0]).tolist(), np.asarray(scores[0]).tolist(), strict=False):
            if score > 0:
                out.append((loaded.chunk_ids[int(idx)], float(score)))
        return out


def highlight(text: str, query: str) -> list[Highlight]:
    wanted = set(tokens(query))
    if not wanted:
        return []
    return [Highlight(start=s, end=e) for s, e, st in token_spans(text) if st in wanted]


def to_hit(chunk: Chunk, score: float, query: str) -> SearchHit:
    return SearchHit(
        document_id=chunk.document_id,
        chunk_id=chunk.id,
        score=round(score, 4),
        heading=chunk.heading,
        text=chunk.text,
        highlights=highlight(chunk.text, query),
    )
