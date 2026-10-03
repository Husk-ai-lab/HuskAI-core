"""One facade over the harness memory for an organisation.

The three markdown artefacts:
  1. company profile        (≤30 facts)                   kind=company_profile, scope=org
  2. network summary         one per automation            kind=network_summary, scope=<automation>
  3. network notes           the notes space per automation kind=network_notes,  scope=<automation>
During discovery many candidate networks exist; afterwards one is kept per automation.
"""

from __future__ import annotations

from app.core.knowledge.memory.company_profile import CompanyProfileManager
from app.core.knowledge.memory.notes import NetworkManager
from app.gateways.sqlite import Database
from app.gateways.sqlite.repositories import DocumentRepo, MemoryRepo


class HarnessMemory:
    def __init__(self, db: Database, org_id: str, max_facts: int = 30):
        self.repo = MemoryRepo(db, org_id)
        known = {d.id for d in DocumentRepo(db, org_id).list()}
        self.profile = CompanyProfileManager(self.repo, max_facts=max_facts)
        self.networks = NetworkManager(self.repo, known)

    def context_for_automation(self, automation_id: str, include_notes: bool = False, lang: str = "en") -> str:
        """Builder context order: company profile → summary → (notes, only when needed)."""
        parts = [self.profile.markdown(lang)]
        summary = self.networks.summary(automation_id)
        if summary:
            parts.append(summary)
        if include_notes:
            net = self.repo.network_for_automation(automation_id)
            if net:
                parts.append(self.networks.render(net, lang))
        return "\n\n---\n\n".join(parts)
