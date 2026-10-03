"""Context builders for the explorer's retrieval.

Deep research reads in this order: the notes already written (the scratchpad), then the
seed document, then BM25 neighbours from OTHER documents (where cross-document repetitive
tasks are found).
"""

from __future__ import annotations

from app.core.agents.harness.context import AgentContext
from app.helpers.text import truncate


def document_brief(ctx: AgentContext, document_id: str, max_chars: int = 7000) -> str:
    doc = ctx.knowledge.docs.get(document_id)
    if not doc:
        return ""
    md = ctx.knowledge.docs.markdown(document_id)
    parts = [f"DOCUMENT {doc.id} — {doc.filename} ({doc.kind.value})"]
    profiles = ctx.knowledge.docs.profiles(document_id)
    for p in profiles[:6]:
        cols = ", ".join(f"{c.name}[{c.dtype}, {p.n_rows - c.non_null} empty]" for c in p.columns[:25])
        parts.append(f"Sheet '{p.sheet}': {p.n_rows} rows; columns: {cols}")
    parts.append(truncate(md, max_chars))
    return "\n".join(parts)


def neighbours(ctx: AgentContext, document_id: str, k: int = 6) -> str:
    """Passages in other documents that share vocabulary with this one."""
    chunks = ctx.knowledge.docs.chunks_for(document_id)
    if not chunks:
        return ""
    probe = " ".join(((c.heading or "") + " " + c.text[:300]) for c in chunks[:6])[:1500]
    hits = [h for h in ctx.knowledge.search_chunks(probe, k=k * 3) if h.document_id != document_id][:k]
    return "\n\n".join(
        f"[{h.chunk_id} · {h.document_id}] {h.heading or ''}\n{truncate(h.text, 600)}" for h in hits
    )
