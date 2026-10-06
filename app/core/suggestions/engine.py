"""Suggestions after documents change (augments discovery, does not replace it).

Upload → `SuggestionQueue.enqueue` collects document ids and, 15 s after the LAST upload, runs one
job per organisation (a folder uploaded in several requests is analysed once):

  1. Explorer `analyze` on each new document — updates the company profile (≤30 facts, rule in code).
     Profile changes become one informational `memory_update` suggestion.
  2. Documents with enough signal are matched to the existing automations (BM25 shortlist) and the
     Explorer proposes `modify_automation` / `new_automation` suggestions.
  3. Evidence is checked in code: only passages of that document, confidence ≥ 0.6, no duplicates.

Delete → `on_document_deleted` creates `source_deleted` suggestions (no LLM).

Nothing here changes an automation. Applying a modify suggestion goes through the Builder chat as a
suggestion message, which always ends in a proposal; only an accepted proposal refreshes the notes.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from app.core.agents.builder.agent import BuilderAgent
from app.core.agents.explorer.agent import ExplorerAgent
from app.core.agents.explorer.summarizer import Summarizer
from app.core.agents.harness.context import AgentContext
from app.core.agents.planner.agent import PlannerAgent
from app.core.connectors.service import kinds_used
from app.data.models.automation import Automation, AutomationStatus
from app.data.models.discovery import Opportunity
from app.data.models.document import Document, DocumentStatus
from app.data.models.suggestion import (
    OPEN_STATUSES,
    Suggestion,
    SuggestionEvidence,
    SuggestionKind,
    SuggestionStatus,
)
from app.gateways.sqlite.repositories import AutomationRepo, DiscoveryRepo, SuggestionRepo, UserRepo
from app.helpers.ids import new_id
from app.helpers.logging import get_logger
from app.helpers.text import quote_or_snippet, snippet
from app.i18n import t

if TYPE_CHECKING:
    from app.core.services import Services

log = get_logger("suggestions")

MIN_SIGNAL = 0.15       # same threshold discovery uses for deep research
MIN_CONFIDENCE = 0.6    # below this, no suggestion (better none than a weak one)
MAX_CANDIDATES = 6      # automations shown to the model per document


def channel(org_id: str) -> str:
    return f"suggestions:{org_id}"


_snippet = snippet  # kept for callers/tests


class SuggestionQueue:
    """Debounced, one job at a time per organisation."""

    def __init__(self, services: Services, debounce_s: float):
        self.services = services
        self.debounce_s = debounce_s
        self._pending: dict[str, dict] = {}       # org -> {"user": str, "docs": list[str], "due": float}
        self._processing: set[str] = set()

    def is_processing(self, org_id: str) -> bool:
        return org_id in self._processing or org_id in self._pending

    def cancel_all(self) -> None:
        self._pending.clear()
        for key, task in list(self.services.tasks.items()):
            if key.startswith(("suggest:", "suggest-refresh:")) and not task.done():
                task.cancel()

    def enqueue(self, org_id: str, user_id: str, document_ids: list[str]) -> None:
        if not document_ids:
            return
        loop = asyncio.get_running_loop()
        entry = self._pending.setdefault(org_id, {"user": user_id, "docs": [], "due": 0.0})
        entry["user"] = user_id
        entry["docs"] += [d for d in document_ids if d not in entry["docs"]]
        entry["due"] = loop.time() + self.debounce_s
        self.services.events.publish(channel(org_id), {"type": "processing"})
        self.services.spawn(f"suggest:{org_id}", self._run(org_id))

    async def _run(self, org_id: str) -> None:
        loop = asyncio.get_running_loop()
        while True:
            entry = self._pending.get(org_id)
            if entry is None:
                return
            delay = entry["due"] - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
                continue
            self._pending.pop(org_id, None)
            self._processing.add(org_id)
            try:
                await SuggestionEngine(self.services, org_id, entry["user"]).process(entry["docs"])
            except Exception as exc:  # never break uploads because of suggestions
                log.warning("suggestion job failed org=%s err=%s", org_id, type(exc).__name__)
            finally:
                self._processing.discard(org_id)
                if org_id not in self._pending:
                    self.services.events.publish(channel(org_id), {"type": "idle"})


class SuggestionEngine:
    def __init__(self, services: Services, org_id: str, user_id: str):
        self.services, self.org_id, self.user_id = services, org_id, user_id
        self.repo = SuggestionRepo(services.db, org_id)
        self.lang = UserRepo(services.db).get_settings(user_id).language.value

    def _ctx(self) -> AgentContext:
        return self.services.agent_context(self.org_id, self.user_id)

    def _publish(self, created: list[Suggestion]) -> None:
        if not created:
            return
        top = created[0]
        self.services.events.publish(channel(self.org_id), {
            "type": "created", "count": len(created),
            "top": {"id": top.id, "kind": top.kind.value, "title": top.title},
        })

    def _new(self, kind: SuggestionKind, title: str, **kw) -> Suggestion:  # type: ignore[no-untyped-def]
        return self.repo.save(Suggestion(id=new_id("sug"), org_id=self.org_id, kind=kind, title=title[:160],
                                         created_by=self.user_id, **kw))

    # --- after upload -------------------------------------------------------------------------
    async def process(self, document_ids: list[str]) -> list[Suggestion]:
        discovery = self.services.tasks.get(f"discovery:{self.org_id}")
        if discovery is not None and not discovery.done():
            # a running discovery covers these documents; check again once it is over
            self.services.suggestions.enqueue(self.org_id, self.user_id, document_ids)
            return []
        ctx = self._ctx()
        last_run = DiscoveryRepo(self.services.db, self.org_id).latest_completed()
        covered = set(last_run.document_ids) if last_run else set()
        docs = [d for d in (ctx.knowledge.docs.get(i) for i in document_ids)
                if d and d.status == DocumentStatus.ingested and d.id not in covered]
        if not docs:
            return []
        if last_run is None:
            created = []
            if not self.repo.find_open(SuggestionKind.run_discovery, None, None):
                created.append(self._new(
                    SuggestionKind.run_discovery, t("suggestions.run_discovery_title", self.lang),
                    body=t("suggestions.run_discovery_body", self.lang, count=len(docs)),
                    document_ids=[d.id for d in docs]))
            self._publish(created)
            return created
        if not self.services.llm.configured_for(self.user_id) or self.services.maintenance.blocking:
            log.info("suggestions skipped org=%s (no AI key or maintenance)", self.org_id)
            return []

        explorer = ExplorerAgent(ctx)
        summarizer = Summarizer(ctx)
        automations = AutomationRepo(self.services.db, self.org_id).list()
        before = {f.id: f for f in ctx.memory.repo.facts()}
        created: list[Suggestion] = []
        for doc in docs:
            try:
                card, _ = await summarizer.card_for(doc)  # the same shared card discovery uses (cached)
                if card.signal < MIN_SIGNAL:
                    continue
                candidates = self.candidates(ctx, doc, automations)
                proposal = await explorer.suggest_changes(card, candidates)
            except Exception as exc:
                log.warning("suggestions: document skipped doc=%s err=%s", doc.id, type(exc).__name__)
                continue
            created += self.accept(ctx, doc, proposal.suggestions, candidates)
        created += self._memory_update(ctx, before, docs)
        self._publish(created)
        return created

    def candidates(self, ctx: AgentContext, doc: Document, automations: list[Automation]) -> list[Automation]:
        """BM25 shortlist: automations whose documents share vocabulary with the new one, or whose title does."""
        if not automations:
            return []
        chunks = ctx.knowledge.docs.chunks_for(doc.id)[:6]
        probe = " ".join(((c.heading or "") + " " + c.text[:300]) for c in chunks)[:1500]
        hit_docs = {h.document_id for h in ctx.knowledge.search_chunks(probe, k=30) if h.document_id != doc.id}
        words = set(re.findall(r"\w{5,}", probe.lower()))
        scored = []
        for a in automations:
            overlap = len(hit_docs & set(a.source_document_ids))
            title_hit = len(words & set(re.findall(r"\w{5,}", a.title.lower())))
            if overlap or title_hit:
                scored.append((2 * overlap + title_hit, a))
        scored.sort(key=lambda x: -x[0])
        if not scored and len(automations) <= MAX_CANDIDATES:
            return automations  # small companies: let the model judge them all
        return [a for _, a in scored[:MAX_CANDIDATES]]

    def accept(self, ctx: AgentContext, doc: Document, drafts: list, candidates: list[Automation]) -> list[Suggestion]:
        """Code-side checks: real passages of THIS document, known automation, confidence, no duplicates."""
        chunks = {c.id: c for c in ctx.knowledge.docs.chunks_for(doc.id)}
        by_id = {a.id: a for a in candidates}
        out = []
        for d in drafts:
            kind = d.kind.strip().lower()
            if d.confidence < MIN_CONFIDENCE or kind not in (SuggestionKind.modify_automation, SuggestionKind.new_automation):
                continue
            evidence = []
            for e in d.evidence:
                chunk = chunks.get(e.chunk_id)
                if chunk is None:
                    continue
                quote = quote_or_snippet(e.quote, chunk.text)
                evidence.append(SuggestionEvidence(document_id=doc.id, chunk_id=chunk.id, quote=quote))
            if not evidence:
                continue
            automation_id = None
            if kind == SuggestionKind.modify_automation:
                if d.automation_id not in by_id:
                    continue
                automation_id = d.automation_id
            kind_e = SuggestionKind(kind)
            if self.repo.find_open(kind_e, automation_id, doc.id):
                continue
            out.append(self._new(
                kind_e, d.title, body=d.body, prompt=d.prompt, automation_id=automation_id, document_ids=[doc.id],
                evidence=evidence, confidence=d.confidence,
                hours_per_week=d.hours_per_week if kind_e == SuggestionKind.new_automation else None))
        return out

    def _memory_update(self, ctx: AgentContext, before: dict, docs: list[Document]) -> list[Suggestion]:
        after = {f.id: f for f in ctx.memory.repo.facts()}
        added = [f for i, f in after.items() if i not in before]
        removed = [f for i, f in before.items() if i not in after]
        if not added and not removed:
            return []
        lines = [f"+ {f.text}" for f in added] + [f"− {f.text}" for f in removed]
        return [self._new(
            SuggestionKind.memory_update, t("suggestions.memory_title", self.lang, count=len(added) + len(removed)),
            body="\n".join(lines)[:1500], document_ids=[d.id for d in docs])]

    # --- after delete -------------------------------------------------------------------------
    def on_document_deleted(self, doc: Document) -> list[Suggestion]:
        """Called BEFORE the document row is removed. No LLM: a fixed, localized request."""
        for s in self.repo.list(OPEN_STATUSES):
            if doc.id in s.document_ids and s.kind in (SuggestionKind.modify_automation, SuggestionKind.new_automation):
                s.status = SuggestionStatus.dismissed
                self.repo.save(s)
        memory = self.services.memory(self.org_id)
        created = []
        for a in AutomationRepo(self.services.db, self.org_id).list():
            net = memory.repo.network_for_automation(a.id)
            if doc.id not in a.source_document_ids and not (net and doc.id in net.document_ids):
                continue
            if self.repo.find_open(SuggestionKind.source_deleted, a.id, doc.id):
                continue
            created.append(self._new(
                SuggestionKind.source_deleted, t("suggestions.source_deleted_title", self.lang, title=a.title),
                body=t("suggestions.source_deleted_body", self.lang, document=doc.filename),
                prompt=t("suggestions.source_deleted_prompt", self.lang, document=doc.filename),
                automation_id=a.id, document_ids=[doc.id]))
        self._publish(created)
        return created

    # --- connectors ---------------------------------------------------------------------------
    def connector_problem(self, kind: str, automation: Automation | None, code: str | None) -> Suggestion | None:
        """An automation sends through a connector that is not set up (`code` None) or stopped working (`code`).
        ONE open suggestion per connector; no LLM, no document evidence: a fixed, localized request."""
        if self.repo.find_open_connector(kind):
            return None
        name = t(f"connectors.name_{kind}", self.lang)
        if code:
            title = t("suggestions.connector_error_title", self.lang, connector=name)
            body = t("suggestions.connector_error_body", self.lang, connector=name, reason=t(f"connectors.error_{code}", self.lang))
        else:
            title = t("suggestions.connector_setup_title", self.lang, connector=name)
            body = t("suggestions.connector_setup_body", self.lang, connector=name,
                     automation=automation.title if automation else t("suggestions.connector_some_automation", self.lang))
        s = self._new(SuggestionKind.configure_connector, title, body=body, connector=kind,
                      automation_id=automation.id if automation else None)
        self._publish([s])
        return s

    def check_connectors(self, a: Automation) -> list[Suggestion]:
        """Called when an automation is saved, activated or drafted."""
        created = []
        for kind in kinds_used(a.graph):
            cn = self.services.connectors.get(self.org_id, kind)
            if cn.alive:
                continue
            s = self.connector_problem(kind.value, a, cn.last_error if cn.state.value == "error" else None)
            if s:
                created.append(s)
        return created

    def connector_ready(self, kind: str) -> None:
        """The connector works now: its open suggestions are done."""
        while (s := self.repo.find_open_connector(kind)) is not None:
            self.repo.set_status(s.id, SuggestionStatus.applied)

    # --- applying -----------------------------------------------------------------------------
    async def create_draft(self, s: Suggestion) -> Automation:
        """new_automation: card notes → a Planner brief (summary depth) → the Builder drafts (like discovery 6–7)."""
        ctx = self._ctx()
        explorer, builder = ExplorerAgent(ctx), BuilderAgent(ctx)
        net = ctx.memory.networks.create(f"sugg_{s.id}", s.title, list(s.document_ids))
        cards = await self._card_notes(ctx, explorer, net, s.document_ids)
        net = ctx.memory.networks.get(net.id) or net
        alias_of = {d: f"D{i + 1:02d}" for i, d in enumerate(net.document_ids)}
        briefs = await PlannerAgent(ctx).briefs({"N1": (net, [])}, cards, alias_of)
        brief = briefs.get("N1")
        opp = Opportunity(id=new_id("opp"), org_id=self.org_id, run_id=net.run_id, network_id=net.id, title=s.title,
                          explanation=s.body or s.title, source_document_ids=net.document_ids,
                          hours_per_week=(brief.hrs if brief else s.hours_per_week) or 0,
                          builder_prompt=brief.prompt if brief else s.prompt or s.body)
        draft, graph = await builder.draft(opp, net)
        auto = Automation(
            id=new_id("auto"), org_id=self.org_id, title=draft.title or s.title,
            description=draft.description or s.body, status=AutomationStatus.bozza, graph=graph,
            source_document_ids=net.document_ids, hours_per_week=opp.hours_per_week, network_id=net.id,
            run_id=None,  # not tied to a discovery run: "Analyze again" must not retire it
        )
        repo = AutomationRepo(self.services.db, self.org_id)
        repo.save(auto, change_note=t("suggestions.change_note_draft", self.lang), author="builder")
        self.check_connectors(auto)
        ctx.memory.networks.keep(net.id, auto.id)
        ctx.memory.networks.write_summary(auto.id, net.id, await builder.summarize(auto.title, net))
        s.status, s.automation_id = SuggestionStatus.applied, auto.id
        self.repo.save(s)
        return auto

    async def refresh_network(self, s: Suggestion) -> None:
        """After an accepted suggestion proposal: bring the new document into the automation's notes."""
        if not s.automation_id:
            return
        ctx = self._ctx()
        autos = AutomationRepo(self.services.db, self.org_id)
        auto = autos.get(s.automation_id)
        net = ctx.memory.repo.network_for_automation(s.automation_id)
        if auto is None or net is None:
            return
        await self._card_notes(ctx, ExplorerAgent(ctx), net, s.document_ids)
        net = ctx.memory.networks.keep(net.id, auto.id)  # re-renders the notes markdown
        ctx.memory.networks.write_summary(auto.id, net.id, await BuilderAgent(ctx).summarize(auto.title, net))
        auto.source_document_ids = list(dict.fromkeys(auto.source_document_ids + [
            d for d in s.document_ids if ctx.knowledge.docs.get(d)]))
        autos.save(auto, new_version=False)

    @staticmethod
    async def _card_notes(ctx: AgentContext, explorer: ExplorerAgent, net, document_ids: list[str]) -> dict:  # type: ignore[no-untyped-def]
        """Bring documents into a network from their cards (cached; no extra model call beyond a missing card)."""
        summarizer = Summarizer(ctx)
        cards = {}
        for doc_id in document_ids:
            doc = ctx.knowledge.docs.get(doc_id)
            if doc:
                card, _ = await summarizer.card_for(doc)
                cards[doc_id] = card
                explorer.card_notes(net, card)
        return cards
