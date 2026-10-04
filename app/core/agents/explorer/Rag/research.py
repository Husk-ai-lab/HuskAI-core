"""Context builders for the explorer's retrieval.

Deep dives (discovery step 4, level L2) read the card, the passages it cites, then BM25
neighbours from OTHER documents (where cross-document repetitive tasks are found).
"""

from __future__ import annotations

from app.core.agents.harness.context import AgentContext
from app.helpers.text import truncate


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
