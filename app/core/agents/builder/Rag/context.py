"""Builder retrieval order (the harness rule):

  1. company profile (always, ≤30 facts)
  2. this automation's network summary (always)
  3. this automation's network notes — only when the request touches other documents
  4. BM25 / data tools on demand
"""

from __future__ import annotations

import re

from app.core.agents.harness.context import AgentContext

_OTHER_DOCS = re.compile(
    r"(anche|altr[oaie]|aggiung|collega|confront|incroci|document|file|foglio|report|distinta|ordine|procedur|"
    r"reparto|magazzino|acquisti|qualità|fornitor|gestionale|mes|erp|"
    r"also|other|add|link|compare|cross|sheet|bill of materials|order|procedure|department|warehouse|"
    r"purchasing|quality|supplier)",
    re.I,
)


def needs_notes(user_request: str) -> bool:
    return bool(_OTHER_DOCS.search(user_request))


def builder_context(ctx: AgentContext, automation_id: str, user_request: str) -> str:
    return ctx.memory.context_for_automation(automation_id, include_notes=needs_notes(user_request), lang=ctx.language)
