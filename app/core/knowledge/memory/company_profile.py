"""Company profile: at most 30 facts, enforced in code.

A new fact is checked against existing facts:
  * near-duplicate → the existing fact is reinforced (more references, merged evidence)
  * free slot      → added
  * profile full   → it becomes a *side fact*: it replaces the weakest fact only if the
                     judge says it is more important or relevant; otherwise rejected.
Replaced facts are kept in fact_history, so nothing is lost silently.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.knowledge.text_analysis import tokens
from app.data.models.knowledge import (
    CompanyProfile,
    Fact,
    FactCandidate,
    FactDecision,
    FactOutcome,
    MemoryKind,
)
from app.gateways.sqlite.repositories import MemoryRepo
from app.helpers.ids import new_id

Judge = Callable[[FactCandidate, Fact], Awaitable[tuple[bool, str]]]

DUPLICATE_JACCARD = 0.6


def similarity(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def heuristic_judge(candidate: FactCandidate, weakest: Fact) -> tuple[bool, str]:
    cand_strength = candidate.importance + min(len(candidate.evidence), 3) * 0.03
    if cand_strength > weakest.strength() + 0.05:
        return True, f"more important ({cand_strength:.2f} > {weakest.strength():.2f})"
    return False, "not important enough to replace an existing fact"


class CompanyProfileManager:
    def __init__(self, repo: MemoryRepo, max_facts: int = 30):
        self.repo = repo
        self.max_facts = max_facts

    def profile(self) -> CompanyProfile:
        return CompanyProfile(org_id=self.repo.org_id, facts=self.repo.facts(), max_facts=self.max_facts)

    def markdown(self, lang: str = "en") -> str:
        return self.profile().render_markdown(lang)

    def _persist_markdown(self) -> None:
        self.repo.put_markdown(MemoryKind.company_profile, "org", self.markdown())

    async def add(self, candidate: FactCandidate, judge: Judge | None = None) -> FactOutcome:
        facts = self.repo.facts()
        # 1. near-duplicate → reinforce
        for f in facts:
            if similarity(f.text, candidate.text) >= DUPLICATE_JACCARD:
                f.references += 1
                f.importance = max(f.importance, candidate.importance)
                seen = {(e.document_id, e.chunk_id) for e in f.evidence}
                f.evidence += [e for e in candidate.evidence if (e.document_id, e.chunk_id) not in seen][:3]
                self.repo.save_fact(f)
                self._persist_markdown()
                return FactOutcome(decision=FactDecision.duplicate, fact=f, reason="already present: reinforced")
        new = Fact(
            id=new_id("fact"),
            org_id=self.repo.org_id,
            text=candidate.text.strip(),
            category=candidate.category,
            importance=candidate.importance,
            evidence=candidate.evidence[:3],
        )
        # 2. free slot
        if len(facts) < self.max_facts:
            self.repo.save_fact(new)
            self._persist_markdown()
            return FactOutcome(decision=FactDecision.added, fact=new)
        # 3. side fact: replace the weakest only if judged more important/relevant
        weakest = min(facts, key=lambda f: f.strength())
        ok, reason = await (judge or heuristic_judge)(candidate, weakest)
        if not ok:
            return FactOutcome(decision=FactDecision.rejected, reason=reason)
        self.repo.replace_fact(weakest, new, reason)
        self._persist_markdown()
        return FactOutcome(decision=FactDecision.replaced, fact=new, replaced=weakest, reason=reason)

    def touch(self, fact_ids: list[str]) -> None:
        """Agents consulted these facts: they get harder to evict."""
        for f in self.repo.facts():
            if f.id in fact_ids:
                f.references += 1
                self.repo.save_fact(f)
