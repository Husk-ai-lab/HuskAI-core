"""Everything an internal agent needs, scoped to one organisation and one user."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.knowledge.memory import HarnessMemory
from app.core.knowledge.service import KnowledgeService
from app.gateways.regolo.provider import LLMProvider, UsageContext
from app.gateways.sqlite import Database
from app.gateways.studio.client import StudioClient
from app.helpers.settings import Settings


@dataclass
class AgentContext:
    settings: Settings
    db: Database
    org_id: str
    user_id: str
    knowledge: KnowledgeService
    memory: HarnessMemory
    llm: LLMProvider
    studio: StudioClient | None = None
    language: str = "en"  # the language agents write user-facing text in (user setting)

    @property
    def language_name(self) -> str:
        from app.i18n import language_name

        return language_name(self.language)

    def write_in(self) -> str:
        """Instruction appended to every agent system prompt."""
        from app.i18n import t

        return t("agent.write_in", "en", language=self.language_name)

    def model(self, temperature: float | None = None) -> BaseChatModel:
        return self.llm.chat_model(UsageContext(self.org_id, self.user_id), temperature=temperature)
