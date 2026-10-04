"""Application services container (one per process), created in the FastAPI lifespan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.agents.harness.context import AgentContext
from app.core.events import EventHub
from app.core.knowledge.index import KnowledgeIndex
from app.core.knowledge.memory import HarnessMemory
from app.core.knowledge.service import KnowledgeService
from app.core.system.maintenance import MaintenanceMonitor
from app.gateways.decisions.base import DecisionModel
from app.gateways.neon import NeonGateway
from app.gateways.regolo.key_store import UserAiKeyStore
from app.gateways.regolo.provider import LLMProvider
from app.gateways.sqlite import Database
from app.gateways.studio.client import StudioClient
from app.gateways.zitadel.oidc import ZitadelClient
from app.gateways.zitadel.token_store import TokenStore
from app.helpers.paths import DataPaths
from app.helpers.settings import Settings


@dataclass
class Services:
    settings: Settings
    paths: DataPaths
    db: Database
    index: KnowledgeIndex
    llm: LLMProvider
    decisions: DecisionModel
    ai_keys: UserAiKeyStore
    neon: NeonGateway
    studio: StudioClient
    zitadel: ZitadelClient
    tokens: TokenStore
    events: EventHub
    maintenance: MaintenanceMonitor
    version: str
    scheduler: object | None = None  # LocalScheduler, set in lifespan
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _suggestions: object | None = None

    @property
    def suggestions(self):  # type: ignore[no-untyped-def]
        """Debounced suggestion jobs after uploads (created lazily: it imports the agents)."""
        if self._suggestions is None:
            from app.core.suggestions.engine import SuggestionQueue

            self._suggestions = SuggestionQueue(self, self.settings.suggestions_debounce_s)
        return self._suggestions

    def knowledge(self, org_id: str) -> KnowledgeService:
        return KnowledgeService(self.db, self.paths, self.index, org_id, max_documents=self.settings.max_documents)

    def memory(self, org_id: str) -> HarnessMemory:
        return HarnessMemory(self.db, org_id, max_facts=self.settings.max_company_facts)

    def agent_context(self, org_id: str, user_id: str, language: str | None = None) -> AgentContext:
        if language is None:
            from app.gateways.sqlite.repositories import UserRepo

            language = UserRepo(self.db).get_settings(user_id).language.value
        return AgentContext(
            settings=self.settings,
            db=self.db,
            org_id=org_id,
            user_id=user_id,
            knowledge=self.knowledge(org_id),
            memory=self.memory(org_id),
            llm=self.llm,
            decisions=self.decisions,
            studio=self.studio,
            language=language,
        )

    def spawn(self, key: str, coro) -> asyncio.Task:  # type: ignore[no-untyped-def]
        existing = self.tasks.get(key)
        if existing and not existing.done():
            coro.close()
            return existing
        task = asyncio.create_task(coro, name=key)
        self.tasks[key] = task
        task.add_done_callback(lambda _t: self.tasks.pop(key, None) if self.tasks.get(key) is _t else None)
        return task
