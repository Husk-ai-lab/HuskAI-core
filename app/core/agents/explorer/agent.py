"""Explorer: the discovery agent (the plan's "Scout" role).

It ranks documents by automation signal, researches the strongest ones in depth writing
notes into candidate knowledge networks, maps cross-document handoffs and proposes
opportunities with estimated hours.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import BaseMessage

from app.core.agents.explorer import prompts
from app.core.agents.explorer.Rag.research import document_brief, neighbours
from app.core.agents.explorer.Rag.signal import heuristic_signal
from app.core.agents.explorer.schemas import (
    DocumentAnalysis,
    HandoffMap,
    NotesExtraction,
    OpportunityMap,
    SuggestionMap,
)
from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.llm import human, structured, system
from app.core.agents.harness.loop import run_tool_loop
from app.core.agents.harness.tools import data_tools, knowledge_tools, memory_read_tools, memory_write_tools
from app.core.knowledge.memory.notes import NoteInvalid
from app.data.models.automation import Automation
from app.data.models.document import Document
from app.data.models.knowledge import DocumentSignal, EvidenceRef, FactCandidate, Network
from app.helpers.logging import get_logger
from app.helpers.text import truncate

log = get_logger("explorer")
Log = Callable[[str], Awaitable[None]]


class ExplorerAgent:
    name = "explorer"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def _sys(self, extra: str = "") -> list[BaseMessage]:
        return [system(prompts.SYSTEM + "\n" + self.ctx.write_in() + ("\n" + extra if extra else ""))]

    # --- step 3: extract + signal ranking -----------------------------------------------------
    async def analyze(self, run_id: str, doc: Document, hint: str | None = None) -> DocumentSignal:
        ks = self.ctx.knowledge
        md = ks.docs.markdown(doc.id)
        profiles = ks.docs.profiles(doc.id)
        periodicity = []
        if doc.kind.is_tabular:
            tools = ks.tabular()
            for p in profiles[:4]:
                try:
                    periodicity.append(tools.detect_periodicity(doc.id, sheet=p.sheet))
                except Exception:
                    pass
        h_score, h_reasons = heuristic_signal(doc, md, profiles, periodicity)
        brief = document_brief(self.ctx, doc.id)
        if periodicity:
            brief += "\nData cadence: " + "; ".join(str(p) for p in periodicity if p.get("cadence"))
        msgs = self._sys(f"Hint from the debugger: {hint}" if hint else "") + [
            human(prompts.ANALYZE.format(brief=brief, heuristics=", ".join(h_reasons) or "none"))
        ]
        analysis = await structured(self.ctx.model(), DocumentAnalysis, msgs)
        ks.docs.set_department(doc.id, analysis.department[:40] if analysis.department else None)
        for f in analysis.facts:
            await self.ctx.memory.profile.add(
                FactCandidate(
                    text=f.text, category=f.category, importance=f.importance,
                    evidence=[EvidenceRef(document_id=doc.id, quote=f.quote)],
                )
            )
        score = round(0.4 * h_score + 0.6 * analysis.automation_signal, 3)
        return DocumentSignal(
            org_id=self.ctx.org_id, run_id=run_id, document_id=doc.id, score=score, heuristic_score=h_score,
            llm_score=analysis.automation_signal, doc_type=analysis.doc_type, department=analysis.department,
            reasons=(analysis.signal_reasons or []) + h_reasons,
        )

    # --- step 4: deep research into a candidate network -------------------------------------------
    async def research(self, network: Network, document_id: str, existing_notes: str,
                       log_fn: Log | None = None, hint: str | None = None) -> int:
        ctx = self.ctx
        tools = knowledge_tools(ctx) + data_tools(ctx) + memory_read_tools(ctx) + memory_write_tools(ctx)
        prompt = prompts.RESEARCH.format(
            document_id=document_id, network_id=network.id,
            existing=existing_notes or "(none)", neighbours=neighbours(ctx, document_id) or "(none)",
        )
        msgs = self._sys(f"Hint from the debugger: {hint}" if hint else "") + [human(prompt)]

        async def on_event(kind: str, data: dict) -> None:
            if log_fn and kind == "tool":
                await log_fn(f"{data['name']}")

        try:
            await run_tool_loop(
                ctx.model(), msgs, tools, max_iterations=ctx.settings.agent_max_iterations, on_event=on_event,
                config={"run_name": "explorer.research", "metadata": {"network_id": network.id}},
            )
        except Exception as exc:
            # Some models don't support tool calling: fall back to one structured extraction.
            log.info("tool loop unavailable (%s), using structured extraction", type(exc).__name__)
            await self._extract_fallback(network, document_id)
        written = ctx.memory.networks.notes(network.id)
        if not written:
            await self._extract_fallback(network, document_id)
            written = ctx.memory.networks.notes(network.id)
        return len(written)

    async def _extract_fallback(self, network: Network, document_id: str) -> None:
        ctx = self.ctx
        msgs = self._sys() + [human(prompts.EXTRACT_FALLBACK.format(
            brief=document_brief(ctx, document_id), neighbours=neighbours(ctx, document_id) or "(none)"))]
        result = await structured(ctx.model(), NotesExtraction, msgs)
        title_to_id: dict[str, str] = {}
        for d in result.notes:
            try:
                note = ctx.memory.networks.write_note(
                    network.id, title=d.title, claim=d.claim, kind=d.kind, frequency=d.frequency, actors=d.actors,
                    hours_per_week=d.hours_per_week,
                    evidence=[EvidenceRef(document_id=d.evidence_document_id, chunk_id=d.evidence_chunk_id, quote=d.quote)],
                )
                title_to_id[d.title.lower()] = note.id
            except NoteInvalid as exc:
                log.debug("fallback note rejected: %s", exc.errors)
        for d in result.notes:
            a = title_to_id.get(d.title.lower())
            for t in d.related_titles:
                b = title_to_id.get(t.lower())
                if a and b and a != b:
                    ctx.memory.networks.link(a, b)

    # --- step 5: cross-network handoffs -----------------------------------------------------------
    async def map_handoffs(self, networks: list[Network]) -> int:
        notes = [n for net in networks for n in self.ctx.memory.networks.notes(net.id)]
        if len(notes) < 2:
            return 0
        listing = "\n".join(
            f"- {n.id} | {n.title} | doc: {','.join(e.document_id for e in n.evidence)} | departments: {', '.join(n.actors)}"
            for n in notes[:120]
        )
        result = await structured(self.ctx.model(), HandoffMap, self._sys() + [human(prompts.HANDOFFS.format(notes=listing))])
        ids = {n.id for n in notes}
        made = 0
        for link in result.links:
            if link.note_a in ids and link.note_b in ids and link.note_a != link.note_b:
                self.ctx.memory.networks.link(link.note_a, link.note_b)
                made += 1
        return made

    # --- step 6: impact --------------------------------------------------------------------------
    async def propose_opportunities(self, networks: list[Network], max_opps: int) -> OpportunityMap:
        notes = [n for net in networks for n in self.ctx.memory.networks.notes(net.id)]
        lang = self.ctx.language
        listing = "\n\n".join(truncate(n.render_markdown(lang), 900) for n in notes[:80])
        msgs = self._sys() + [human(prompts.IMPACT.format(
            max_opps=max_opps, profile=self.ctx.memory.profile.markdown(lang), notes=listing or "(none)"))]
        result = await structured(self.ctx.model(), OpportunityMap, msgs)
        known = {n.id for n in notes}
        for o in result.opportunities:
            o.note_ids = [i for i in o.note_ids if i in known]
        result.opportunities = [o for o in result.opportunities if o.note_ids][:max_opps]
        return result

    # --- suggestions: a document added after discovery --------------------------------------------
    async def suggest_changes(self, doc: Document, automations: list[Automation]) -> SuggestionMap:
        """Hypotheses only: the engine validates evidence and the Builder verifies before any change."""
        ctx = self.ctx
        chunks = ctx.knowledge.docs.chunks_for(doc.id)[:24]
        passages = "\n\n".join(f"[{c.id}] {c.heading or ''}\n{truncate(c.text, 700)}" for c in chunks)
        listing = "\n".join(
            f"- {a.id} · {a.title} · {truncate(a.description, 240)} · docs: {', '.join(a.source_document_ids) or '-'}"
            for a in automations
        )
        msgs = self._sys() + [human(prompts.SUGGEST.format(
            passages=f"DOCUMENT {doc.id} — {doc.filename}\n{passages}" if passages else "(empty)",
            automations=listing or "(none)", profile=ctx.memory.profile.markdown(ctx.language)))]
        return await structured(ctx.model(), SuggestionMap, msgs)
