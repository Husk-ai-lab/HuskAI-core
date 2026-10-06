"""LLM access through husk-gateway (`POST /v1/chat/completions`, OpenAI-compatible, proxied to Regolo.ai).

No AI key exists on this computer: every call carries the signed-in user's Clerk access token (see client.py).
The gateway checks that the organisation has paid and meters the tokens; usage is also recorded locally per
org/user/day/model for the Settings screen and the per-run report."""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import LLMResult

from app.core.metering import MeterCallback, usage_from
from app.gateways.gateway.client import GatewayClient
from app.gateways.sqlite.repositories import UsageRepo
from app.helpers.logging import get_logger
from app.helpers.settings import Settings
from app.i18n.errors import LocalizedError

log = get_logger("llm")


class LLMNotConfigured(LocalizedError):
    status = 401


class UsageContext:
    """Who pays for a call: recorded locally per org/user/day/model."""

    def __init__(self, org_id: str, user_id: str):
        self.org_id, self.user_id = org_id, user_id


class LLMProvider(Protocol):
    model_name: str | None

    def chat_model(self, usage: UsageContext | None = None, temperature: float | None = None,
                   model: str | None = None, reasoning: str | None = None, max_tokens: int | None = None,
                   timeout: float | None = None, max_retries: int | None = None) -> BaseChatModel: ...

    @property
    def summary_model_name(self) -> str | None: ...

    def configured_for(self, user_id: str) -> bool: ...


class _UsageCallback(BaseCallbackHandler):
    def __init__(self, repo: UsageRepo, usage: UsageContext, model: str):
        self.repo, self.usage, self.model = repo, usage, model

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tin, tout = usage_from(response)
        try:
            self.repo.add(self.usage.org_id, self.usage.user_id, self.model, tin, tout)
        except Exception as exc:  # usage must never break a call
            log.warning("usage record failed: %s", type(exc).__name__)


class GatewayProvider:
    def __init__(self, settings: Settings, usage_repo: UsageRepo, gateway: GatewayClient):
        self.settings, self.usage_repo, self.gateway = settings, usage_repo, gateway
        self.model_name: str | None = settings.llm_model

    @property
    def summary_model_name(self) -> str | None:
        """The smaller model for document summaries (gpt-oss-20b); None = use the main model."""
        return self.settings.summary_model

    def configured_for(self, user_id: str) -> bool:
        return self.gateway.signed_in(user_id)

    def chat_model(self, usage: UsageContext | None = None, temperature: float | None = None,
                   model: str | None = None, reasoning: str | None = None, max_tokens: int | None = None,
                   timeout: float | None = None, max_retries: int | None = None) -> BaseChatModel:
        """`reasoning`: low | medium (never high), forwarded to the model by the gateway."""
        if usage is None or not self.gateway.signed_in(usage.user_id):
            raise LLMNotConfigured("errors.session_expired")
        from langchain_openai import ChatOpenAI

        model_id = model or self.model_name or self.settings.llm_model
        callbacks: list[BaseCallbackHandler] = [MeterCallback(model_id), _UsageCallback(self.usage_repo, usage, model_id)]
        extra: dict[str, Any] = {}
        if reasoning:
            extra["reasoning_effort"] = reasoning
        if max_tokens:
            extra["max_tokens"] = max_tokens
        return ChatOpenAI(
            model=model_id,
            api_key="gateway",  # placeholder: the real token is injected per request by GatewayTransport
            base_url=self.gateway.base_url + "/v1",
            http_async_client=self.gateway.http_for(usage.user_id),
            temperature=self.settings.llm_temperature if temperature is None else temperature,
            timeout=timeout or self.settings.llm_timeout_s,
            max_retries=4 if max_retries is None else max_retries,  # 429 / unstable networks: SDK back-off
            callbacks=callbacks,
            stream_usage=True,
            **extra,
        )
