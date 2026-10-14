"""Application services container (one per process), created in the FastAPI lifespan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.agents.harness.context import AgentContext
from app.core.connectors.service import ConnectorService
from app.core.events import EventHub
from app.core.knowledge.index import KnowledgeIndex
from app.core.knowledge.memory import HarnessMemory
from app.core.knowledge.service import KnowledgeService
from app.core.shell_bridge import ShellBridge
from app.core.system.maintenance import MaintenanceMonitor
from app.gateways.clerk.login import LoginBroker
from app.gateways.clerk.oidc import ClerkClient
from app.gateways.clerk.tokens import AccessTokens, TokenStore
from app.gateways.decisions.base import DecisionModel
from app.gateways.gateway.client import GatewayClient
from app.gateways.gateway.llm import LLMProvider
from app.gateways.neon import NeonGateway
from app.gateways.sqlite import Database
from app.gateways.studio.client import StudioClient
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
    gateway: GatewayClient
    connectors: ConnectorService
    neon: NeonGateway
    studio: StudioClient
    clerk: ClerkClient
    tokens: TokenStore
    access: AccessTokens
    events: EventHub
    maintenance: MaintenanceMonitor
    version: str
    logins: LoginBroker = field(default_factory=LoginBroker)
    shell: ShellBridge = field(default_factory=ShellBridge)  # requests Husk makes to the desktop shell
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
            connectors=self.connectors,
            studio=self.studio,
            language=language,
        )

    def connector_check(self, automation, user_id: str) -> None:  # type: ignore[no-untyped-def]
        """After an automation is saved, activated or drafted: a connector it sends through that is not alive becomes
        a suggestion (one per connector). Never raises: it must not break saving an automation."""
        try:
            from app.core.suggestions.engine import SuggestionEngine

            SuggestionEngine(self, automation.org_id, user_id).check_connectors(automation)
        except Exception as exc:
            from app.helpers.logging import get_logger

            get_logger("connectors").warning("connector check failed: %s", type(exc).__name__)

    def spawn(self, key: str, coro) -> asyncio.Task:  # type: ignore[no-untyped-def]
        existing = self.tasks.get(key)
        if existing and not existing.done():
            coro.close()
            return existing
        task = asyncio.create_task(coro, name=key)
        self.tasks[key] = task
        task.add_done_callback(lambda _t: self.tasks.pop(key, None) if self.tasks.get(key) is _t else None)
        return task
