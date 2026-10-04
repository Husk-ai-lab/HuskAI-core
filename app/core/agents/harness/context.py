"""Everything an internal agent needs, scoped to one organisation and one user."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.knowledge.memory import HarnessMemory
from app.core.knowledge.service import KnowledgeService
from app.gateways.decisions.base import DecisionModel
from app.gateways.regolo.provider import LLMProvider, UsageContext
from app.gateways.sqlite import Database
from app.gateways.studio.client import StudioClient
from app.helpers.settings import Settings

# role → (which model, output cap, per-call timeout s, retries). Discovery roles (reasoning low): summary,
# explorer, planner. Summaries are short and must never hold up a step: short timeout, one retry.
ROLES: dict[str, tuple[str, int | None, float | None, int | None]] = {
    "summary": ("summary", 1200, 20.0, 1),
    "explorer": ("main", 8000, None, None),
    "planner": ("main", 6000, None, None),
    "builder": ("main", 4000, None, None),  # output cap = runaway guard (reasoning stays medium)
    "runtime": ("main", 4000, None, None),
}


@dataclass
class AgentContext:
    settings: Settings
    db: Database
    org_id: str
    user_id: str
    knowledge: KnowledgeService
    memory: HarnessMemory
    llm: LLMProvider
    decisions: DecisionModel | None = None  # typed decisions (Jev now, Serenity later)
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

    def model(self, temperature: float | None = None, role: str = "builder") -> BaseChatModel:
        """Model per role (see ROLES): the summary role uses the smaller summary model when the key offers it.
        Reasoning effort: low across discovery, medium for the Builder and automation runs, never high."""
        model_name, max_tokens, timeout, retries = ROLES.get(role, ROLES["builder"])
        override = self.llm.summary_model_name if model_name == "summary" else None
        effort = getattr(self.settings, f"reasoning_{role}", None) or self.settings.reasoning_builder
        return self.llm.chat_model(UsageContext(self.org_id, self.user_id), temperature=temperature, model=override,
                                   reasoning=effort, max_tokens=max_tokens, timeout=timeout, max_retries=retries)
