"""Document cards (discovery step 3): the shared summary memory the Explorer reads first.

* Spreadsheets → `tabular/facts.table_card`: computed facts, no model call.
* Text documents → one structured call on the **summary model** (smaller, e.g. gpt-oss-20b; the main
  model when the key doesn't offer it). Evidence (passage id + verbatim quote) is kept only when it
  points at a real passage of this document; activities without evidence are kept as such.

Cards are cached per document: reused while the file (sha256) and `CARD_VERSION` are unchanged.
"""

from __future__ import annotations

import asyncio
import re

from app.core.agents.explorer import prompts
from app.core.agents.explorer.Rag.signal import heuristic_signal
from app.core.agents.explorer.schemas import CardDraft
from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.llm import human, structured, system
from app.core.knowledge.ingest import title_for
from app.core.knowledge.tabular.facts import table_card
from app.data.models.document import Document
from app.data.models.knowledge import (
    CARD_VERSION,
    CardEvidence,
    CardKind,
    DocumentCard,
    EvidenceRef,
    FactCandidate,
)
from app.gateways.sqlite.repositories import CardRepo
from app.helpers.logging import get_logger
from app.helpers.text import quote_or_snippet, truncate

log = get_logger("summarizer")

MAX_PASSAGE_CHARS = 700
MAX_BRIEF_CHARS = 4000
_SIGNAL_WORDS = re.compile(r"a mano|ricopi|ogni (giorno|settimana|mattina|sera|luned)|manual|controll|sollecit|"
                           r"email|excel|settiman|giornalier|riordin|trascri|every|weekly|daily", re.I)


def _passages(ctx: AgentContext, doc: Document) -> str:
    """First passages up to the cap, then the most signal-dense remaining ones (long documents)."""
    chunks = ctx.knowledge.docs.chunks_for(doc.id)
    picked, size = [], 0
    for c in chunks:
        block = f"[{c.id}] {c.heading or ''}\n{truncate(c.text, MAX_PASSAGE_CHARS)}"
        if size + len(block) > MAX_BRIEF_CHARS * 0.7:
            break
        picked.append(c.id)
        size += len(block)
    rest = sorted((c for c in chunks if c.id not in picked), key=lambda c: -len(_SIGNAL_WORDS.findall(c.text)))
    for c in rest:
        block_len = len(c.text[:MAX_PASSAGE_CHARS]) + 40
        if size + block_len > MAX_BRIEF_CHARS:
            break
        if _SIGNAL_WORDS.search(c.text):
            picked.append(c.id)
            size += block_len
    by_id = {c.id: c for c in chunks}
    ordered = [by_id[i] for i in sorted(picked, key=lambda i: by_id[i].ordinal)]
    return "\n\n".join(f"[{c.id}] {c.heading or ''}\n{truncate(c.text, MAX_PASSAGE_CHARS)}" for c in ordered)


class Summarizer:
    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self.repo = CardRepo(ctx.db, ctx.org_id)

    async def card_for(self, doc: Document) -> tuple[DocumentCard, bool]:
        """(card, built_now). Cached cards are returned without any model call."""
        cached = self.repo.fresh(doc.id, doc.sha256, CARD_VERSION)
        if cached:
            return cached, False
        if doc.kind.is_tabular:
            card = table_card(self.ctx.knowledge, doc, self.ctx.language)
        else:
            card = await self._text_card_safe(doc)
        self.repo.save(card)
        return card, True

    async def _text_card_safe(self, doc: Document) -> DocumentCard:
        """One document never fails or holds up the step: summary model → main model, within
        `summary_deadline_s`; past it (or on failure) a basic card without AI, rebuilt later (`refresh`)."""

        async def attempts() -> DocumentCard:
            for role in ("summary", "explorer"):  # summary model, then the main model (both reasoning low)
                try:
                    return await self.text_card(doc, role=role)
                except Exception as exc:
                    log.warning("card failed doc=%s role=%s err=%s", doc.id, role, type(exc).__name__)
            return self.basic_card(doc)

        try:
            return await asyncio.wait_for(attempts(), timeout=self.ctx.settings.summary_deadline_s)
        except TimeoutError:
            log.warning("card deadline reached doc=%s: basic card now, full card in the background", doc.id)
            return self.basic_card(doc)

    async def refresh(self, doc: Document) -> None:
        """Background: replace a basic card with a full one (no deadline). Used after step 3."""
        for role in ("summary", "explorer"):
            try:
                self.repo.save(await self.text_card(doc, role=role))
                return
            except Exception as exc:
                log.info("background card failed doc=%s role=%s err=%s", doc.id, role, type(exc).__name__)

    def basic_card(self, doc: Document) -> DocumentCard:
        """Fallback: title from the file, first heading/sentence as the summary, heuristic signal. Not cached
        as final: version 0 so the next run tries the model again."""
        md = self.ctx.knowledge.docs.markdown(doc.id)
        signal, reasons = heuristic_signal(doc, md, [], [])
        first = next((ln.strip("# ").strip() for ln in md.splitlines() if len(ln.strip("# ").strip()) > 20), doc.filename)
        return DocumentCard(org_id=self.ctx.org_id, document_id=doc.id, sha256=doc.sha256, version=0, kind=CardKind.text,
                            title=title_for(doc.filename), sentence=truncate(first, 300), signal=round(signal, 3),
                            reasons=reasons, model="fallback")

    async def text_card(self, doc: Document, role: str = "summary") -> DocumentCard:
        ctx = self.ctx
        md = ctx.knowledge.docs.markdown(doc.id)
        h_score, h_reasons = heuristic_signal(doc, md, [], [])
        passages = _passages(ctx, doc)
        msgs = [system(prompts.SYSTEM + "\n" + ctx.write_in()),
                human(prompts.SUMMARIZE.format(passages=f"DOCUMENT {doc.id} — {doc.filename}\n{passages}" if passages
                                               else f"DOCUMENT {doc.id} — {doc.filename}\n(empty)",
                                               heuristics=", ".join(h_reasons) or "none"))]
        model = ctx.model(role=role)
        draft = await structured(model, CardDraft, msgs, prefer="json_schema")
        chunks = {c.id: c for c in ctx.knowledge.docs.chunks_for(doc.id)}
        quotes = [CardEvidence(document_id=doc.id, chunk_id=q.chunk_id, quote=quote_or_snippet(q.quote, chunks[q.chunk_id].text))
                  for q in draft.quotes if q.chunk_id in chunks]
        for f in draft.facts:
            await ctx.memory.profile.add(FactCandidate(text=f.text, category=f.category, importance=f.importance,
                                                       evidence=[EvidenceRef(document_id=doc.id, quote=f.quote)]))
        sig = draft.signals
        dept = sig.dept[0][:40] if sig.dept else None
        if dept:
            ctx.knowledge.docs.set_department(doc.id, dept)
        return DocumentCard(
            org_id=ctx.org_id, document_id=doc.id, sha256=doc.sha256, version=CARD_VERSION, kind=CardKind.text,
            title=title_for(doc.filename), sentence=draft.sentence, summary=draft.summary, department=dept,
            signal=round(0.4 * h_score + 0.6 * sig.rep, 3), reasons=h_reasons, quotes=quotes,
            signals={"rep": round(sig.rep, 2), "manual": sig.manual, "freq": sig.freq, "dept": sig.dept,
                     "vol": sig.vol, "sys": sig.sys},
            model=getattr(model, "model_name", None) or getattr(ctx.llm, "summary_model_name", None) or ctx.llm.model_name,
        )
