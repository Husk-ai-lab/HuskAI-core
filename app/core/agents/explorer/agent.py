"""Explorer: the discovery agent (the plan's "Scout" role), confidence-driven over document cards.

Step 4 reads the shared card memory in three levels, each more expensive than the last, and only goes
deeper where its own confidence is low (see docs/agents/EXPLORER_AGENT.md):

  L0 `sketch`     one line per document → candidate activities + which cards to open
  L1 `confirm`    the requested cards (activities A*, computed facts S*) → notes with evidence
  L2 `deep_dive`  ≤ N documents: cited passages + neighbours (text) or a bounded data-tool loop (spreadsheet)

Then it maps handoffs and proposes opportunities, each with a Builder-ready prompt.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import BaseMessage

from app.core.agents.explorer import prompts
from app.core.agents.explorer.schemas import (
    SuggestionMap,
)
from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.llm import human, structured, system
from app.core.knowledge.memory.notes import NoteInvalid
from app.data.models.automation import Automation
from app.data.models.knowledge import DocumentCard, EvidenceRef, Network
from app.gateways.decisions import questions as q
from app.helpers.logging import get_logger
from app.helpers.text import truncate

log = get_logger("explorer")

TABLE_DIVE_ITERATIONS = 4


class ExplorerAgent:
    name = "explorer"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def _sys(self, extra: str = "") -> list[BaseMessage]:
        return [system(prompts.SYSTEM + "\n" + self.ctx.write_in() + ("\n" + extra if extra else ""))]

    def _profile(self) -> str:
        return truncate(self.ctx.memory.profile.markdown(self.ctx.language), 3000)

    # --- one-shot map: every network from the document lines ------------------------------------------
    @staticmethod
    def aliases(cards: list[DocumentCard]) -> dict[str, DocumentCard]:
        return {f"D{i + 1:02d}": c for i, c in enumerate(cards)}

    # --- step 4: link the documents with the decision model ---------------------------------------------
    def cards_state(self, cards: list[DocumentCard], alias_of: dict[str, str]) -> str:
        """The decision model's state: every card as one line + its summary (compact, not JSON)."""
        out = []
        for c in cards:
            out.append(c.line(alias_of[c.document_id]))
            if c.summary:
                out.append("   " + truncate(c.summary, 420))
        return "\n".join(out)

    def candidate_pairs(self, cards: list[DocumentCard], alias_of: dict[str, str]) -> list[tuple[str, str]]:
        """BM25 neighbours (top 4 per card) first, then shared departments/systems. ≤ 4 × documents, no n²."""
        known = {c.document_id for c in cards}
        cap = 4 * len(cards)
        pairs: dict[tuple[str, str], None] = {}

        def add(x: str, y: str) -> None:
            if x != y and len(pairs) < cap:
                pairs[tuple(sorted((alias_of[x], alias_of[y])))] = None  # type: ignore[index]

        for c in cards:
            probe = truncate(f"{c.title} {c.summary or c.sentence}", 600)
            seen: list[str] = []
            for h in self.ctx.knowledge.search_chunks(probe, k=24):
                if h.document_id in known and h.document_id != c.document_id and h.document_id not in seen:
                    seen.append(h.document_id)
                if len(seen) == 4:
                    break
            for d in seen:
                add(c.document_id, d)
        def tags(c: DocumentCard) -> set[str]:
            vals = list(c.signals.get("dept") or []) + list(c.signals.get("sys") or [])
            return {str(v).strip().lower() for v in vals if str(v).strip()}
        for i, a in enumerate(cards):
            for b in cards[i + 1:]:
                if tags(a) & tags(b):
                    add(a.document_id, b.document_id)
        return list(pairs)

    async def link_documents(self, cards: list[DocumentCard], run_id: str) -> dict[str, Any]:
        """doc_related for every candidate pair, relation_type for kept edges, networks = connected components
        (split above `max_network_docs` by cutting the weakest edges); strong singletons become their own network."""
        ctx = self.ctx
        lang = ctx.language
        by_alias = self.aliases(cards)
        alias_of = {c.document_id: a for a, c in by_alias.items()}
        state = self.cards_state(cards, alias_of)
        pairs = self.candidate_pairs(cards, alias_of)
        related = await ctx.decisions.ask(ctx.user_id, ctx.org_id, state,
                                          {f"rel_{a}_{b}": q.doc_related(a, b, lang) for a, b in pairs}) if pairs else {}
        gate = ctx.settings.gate_related
        kept = [(a, b, related[f"rel_{a}_{b}"].p or 0.0) for a, b in pairs
                if f"rel_{a}_{b}" in related and (related[f"rel_{a}_{b}"].p or 0.0) >= gate]
        types = await ctx.decisions.ask(ctx.user_id, ctx.org_id, state,
                                        {f"type_{a}_{b}": q.relation_type(a, b, lang) for a, b, _ in kept}) if kept else {}
        edges = [{"a": a, "b": b, "p": round(p, 3), "type": (types[f"type_{a}_{b}"].choice if f"type_{a}_{b}" in types else None)}
                 for a, b, p in kept]
        groups = _components(edges, list(by_alias), max_size=ctx.settings.max_network_docs)
        groups = [g for g in groups if len(g) > 1 or by_alias[g[0]].signal >= 0.6]
        networks: dict[str, Any] = {}
        for i, group in enumerate(sorted(groups, key=lambda g: -max(by_alias[a].signal for a in g))):
            members = sorted(group, key=lambda a: -by_alias[a].signal)
            net = ctx.memory.networks.create(run_id, by_alias[members[0]].title, [by_alias[a].document_id for a in members])
            first_note: dict[str, str] = {}
            for a in members:
                if self.card_notes(net, by_alias[a]):
                    first_note[a] = ctx.memory.networks.notes(net.id)[-1].id
            for e in edges:
                if e["a"] in first_note and e["b"] in first_note:
                    ctx.memory.networks.link(first_note[e["a"]], first_note[e["b"]])
            networks[f"N{i + 1}"] = {"net_id": net.id, "docs": members,
                                     "edges": [e for e in edges if e["a"] in group and e["b"] in group]}
        return {"pairs": len(pairs), "edges": edges, "networks": networks, "aliases": {a: c.document_id for a, c in by_alias.items()}}

    # --- step 5: assess each network with the decision model -----------------------------------------
    async def assess(self, networks: dict[str, Any], cards: list[DocumentCard]) -> dict[str, Any]:
        """Per network (parallel): repetitiveness (score), hours (8 buckets/month), deeper research per document."""
        ctx = self.ctx
        lang = ctx.language
        by_alias = self.aliases(cards)
        alias_of = {c.document_id: a for a, c in by_alias.items()}

        async def one(nid: str, info: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            members = [by_alias[a] for a in info["docs"] if a in by_alias]
            links = "\n".join(f"{e['a']} ↔ {e['b']}: {q.RELATIONS.get(e['type'] or '', ('', e['type'] or '-'))[1 if lang != 'it' else 0]}"
                              for e in info["edges"])
            state = self.cards_state(members, alias_of) + (f"\nLinks:\n{links}" if links else "")
            questions = {"repetitive": q.repetitiveness(lang), "hours": q.hours_wasted(lang)}
            questions |= {f"deeper_{a}": q.needs_deeper_research(a, lang) for a in info["docs"]}
            ans = await ctx.decisions.ask(ctx.user_id, ctx.org_id, state, questions)
            rep = q.score_value(ans["repetitive"].probabilities) if "repetitive" in ans else 0.0
            hours, spread = q.expected_hours_per_week(ans["hours"].probabilities) if "hours" in ans else (0.0, 0.0)
            deeper = [a for a in info["docs"] if (ans.get(f"deeper_{a}") and (ans[f"deeper_{a}"].p or 0) >= ctx.settings.gate_deeper)]
            return nid, {"repetitive": rep, "hours_per_week": hours, "hours_spread": spread, "deeper": deeper,
                         "hours_probs": ans["hours"].probabilities if "hours" in ans else {}}

        results = await asyncio.gather(*(one(nid, info) for nid, info in networks.items()))
        return dict(results)

    # --- card → notes without AI (Suggestions: bring a new document into a network) ----------------------
    def card_notes(self, network: Network, card: DocumentCard) -> int:
        """One complete note from the card's summary, evidence = its key quotes (or computed facts)."""
        from app.i18n import t

        claim = (card.summary or card.sentence).strip()
        if claim and claim[-1] not in ".!?»)":
            claim += "."
        evidence = [q.to_ref() for q in card.quotes] or [EvidenceRef(document_id=card.document_id, quote=truncate(card.sentence, 300))]
        dept = card.signals.get("dept") or ([card.department] if card.department else [])
        try:
            self.ctx.memory.networks.write_note(
                network.id, title=card.title, claim=claim, frequency=str(card.signals.get("freq") or "") or t("cards.freq_not_stated", self.ctx.language),
                actors=list(dept)[:3], evidence=evidence[:4])
            return 1
        except NoteInvalid as exc:
            log.debug("card note rejected: %s", exc.errors)
            return 0

    # --- suggestions: a document added after discovery --------------------------------------------
    async def suggest_changes(self, card: DocumentCard, automations: list[Automation]) -> SuggestionMap:
        """Hypotheses only: the engine validates evidence and the Builder verifies before any change.
        Reads the new document's card plus the passages it cites (not the whole document)."""
        ctx = self.ctx
        cited = [q.chunk_id for q in card.quotes if q.chunk_id]
        chunks = [c for c in (ctx.knowledge.docs.chunk(i) for i in dict.fromkeys(cited)) if c]
        if not chunks:
            chunks = ctx.knowledge.docs.chunks_for(card.document_id)[:8]
        passages = "\n\n".join(f"[{c.id}] {c.heading or ''}\n{truncate(c.text, 700)}" for c in chunks)
        listing = "\n".join(
            f"- {a.id} · {a.title} · {truncate(a.description, 240)} · docs: {', '.join(a.source_document_ids) or '-'}"
            for a in automations
        )
        body: dict[str, Any] = {"passages": f"{card.line()}\n{card.summary}\n\nDOCUMENT {card.document_id}\n{passages}" if passages
                                else "(empty)", "automations": listing or "(none)", "profile": self._profile()}
        msgs = self._sys() + [human(prompts.SUGGEST.format(**body))]
        return await structured(ctx.model(role="explorer"), SuggestionMap, msgs)


def _components(edges: list[dict[str, Any]], nodes: list[str], max_size: int = 8) -> list[list[str]]:
    """Connected components; a component above `max_size` is split by dropping its weakest edges."""
    def comps(es: list[dict[str, Any]]) -> list[list[str]]:
        parent = {n: n for n in nodes}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for e in es:
            parent[find(e["a"])] = find(e["b"])
        groups: dict[str, list[str]] = {}
        for n in nodes:
            groups.setdefault(find(n), []).append(n)
        return list(groups.values())

    active = sorted(edges, key=lambda e: e["p"])  # weakest first
    groups = comps(active)
    while any(len(g) > max_size for g in groups) and active:
        big = next(g for g in groups if len(g) > max_size)
        weakest = next(e for e in active if e["a"] in big and e["b"] in big)
        active.remove(weakest)
        groups = comps(active)
    return groups
