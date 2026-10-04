"""Planner agent: context gathered in code, briefs in parallel (keep / depth / hours are decided by the decision model).

    context  the network's card summaries + BM25 passages only from documents the decision model flagged
    brief    one call per kept network, in parallel → Builder prompt and refreshed notes (hours come from
             the decision model, not from the LLM)

Reasoning effort is low (discovery role "planner"); the Builder keeps medium.
"""

from __future__ import annotations

import asyncio

from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.llm import human, structured, system
from app.core.agents.planner import prompts
from app.core.agents.planner.schemas import Brief
from app.core.knowledge.memory.notes import NoteInvalid
from app.data.models.knowledge import DocumentCard, EvidenceRef, Network
from app.helpers.logging import get_logger
from app.helpers.text import quote_or_snippet, truncate
from app.i18n import t

log = get_logger("planner")

MAX_BM25 = 4
PASSAGES_PER_QUERY = 4
BRIEF_CONCURRENCY = 4


class PlannerAgent:
    name = "planner"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def _sys(self, extra: str = "") -> list:
        return [system(prompts.SYSTEM + "\n" + self.ctx.write_in() + ("\n" + extra if extra else ""))]

    def describe(self, nid: str, net: Network, alias_of: dict[str, str]) -> str:
        notes = self.ctx.memory.networks.notes(net.id)
        acts = "; ".join(f"{n.claim} ({n.frequency or '-'}, {', '.join(n.actors) or '-'}"
                         + (f", ~{n.hours_per_week:g} h/wk" if n.hours_per_week else "") + ")" for n in notes)
        docs = ",".join(alias_of.get(d, d) for d in net.document_ids)
        return f"{nid} · {net.label} · {docs} · {truncate(acts, 900)}"

    def context(self, net: Network, deeper: list[str], cards: dict[str, DocumentCard], alias_of: dict[str, str]) -> str:
        """Gathered in code, no model call: every card's summary, plus BM25 passages ONLY from the documents
        the decision model flagged for deeper research."""
        parts = []
        for d in net.document_ids:
            c = cards.get(d)
            if c:
                parts.append(f"{alias_of.get(d, d)} {c.title}: {c.summary or c.sentence} [{c.signal_text()}]")
        out = "Document summaries:\n" + "\n".join(parts)
        passages, seen = [], set()
        for d in deeper:
            c = cards.get(d)
            probe = truncate(f"{net.label} {c.summary if c else ''}", 500)
            for h in self.ctx.knowledge.search_chunks(probe, k=PASSAGES_PER_QUERY, document_ids=[d]):
                if h.chunk_id not in seen:
                    seen.add(h.chunk_id)
                    passages.append(f"[{h.chunk_id}] ({alias_of.get(d, d)}) {h.heading or ''}\n{truncate(h.text, 600)}")
        if passages:
            out += "\n\nPassages (documents that needed deeper research):\n" + "\n\n".join(passages)
        return out

    async def briefs(self, selected: dict[str, tuple[Network, list[str]]], cards: dict[str, DocumentCard],
                     alias_of: dict[str, str], hint: str | None = None) -> dict[str, Brief]:
        """One call per kept network, in parallel (reasoning low). Failures fall back to the cards later."""
        gate = asyncio.Semaphore(BRIEF_CONCURRENCY)

        async def one(nid: str, net: Network, deeper: list[str]) -> tuple[str, Brief | None]:
            async with gate:
                msgs = self._sys(f"Hint from the debugger: {hint}" if hint else "") + [human(prompts.BRIEF.format(
                    network=self.describe(nid, net, alias_of), context=self.context(net, deeper, cards, alias_of)))]
                try:
                    return nid, await structured(self.ctx.model(role="planner"), Brief, msgs, prefer="json_schema",
                                                 config={"run_name": f"planner.brief.{nid}"})
                except Exception as exc:
                    log.warning("brief failed net=%s err=%s", nid, type(exc).__name__)
                    return nid, None

        results = await asyncio.gather(*(one(nid, net, deeper) for nid, (net, deeper) in selected.items()))
        return {k: v for k, v in results if v is not None}

    def refresh_notes(self, net: Network, brief: Brief, alias_doc: dict[str, str]) -> int:
        """Brief notes replace the network's notes when at least one is valid (rewritten whole, validated)."""
        valid = []
        for n in brief.notes:
            doc_id = alias_doc.get(n.doc)
            if not doc_id:
                continue
            chunk = self.ctx.knowledge.docs.chunk(n.chunk) if n.chunk else None
            if chunk and chunk.document_id != doc_id:
                chunk = None
            quote = quote_or_snippet(n.quote, chunk.text) if chunk else None
            valid.append((n, EvidenceRef(document_id=doc_id, chunk_id=chunk.id if chunk else None, quote=quote)))
        if not valid:
            return 0
        written = 0
        old = self.ctx.memory.networks.notes(net.id)
        for n, ev in valid:
            claim = n.claim.strip() if n.claim.strip()[-1:] in ".!?»)" else n.claim.strip() + "."
            try:
                self.ctx.memory.networks.write_note(net.id, title=n.title, claim=claim,
                                                    frequency=n.freq or t("cards.freq_not_stated", self.ctx.language),
                                                    actors=n.dept, hours_per_week=n.hrs, evidence=[ev])
                written += 1
            except NoteInvalid as exc:
                log.debug("brief note rejected: %s", exc.errors)
        if written:
            for o in old:  # the brief's notes are the complete, better version
                self.ctx.memory.repo.delete_note(o.id)
        return written
