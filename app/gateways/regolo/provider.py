"""LLM access.

Today: Regolo.ai's OpenAI-compatible API, called directly with the user's key, entered once
after login, verified with Regolo and saved in the user's local data (`key_store.py`).
`REGOLO_API_KEY` in `.env` remains a developer fallback. Both are a temporary MVP deviation
from rule 3. Tomorrow: the same interface
points at husk-gateway (`https://api.husk-ai.com/v1`) with the user's ZITADEL token,
and nothing above this file changes.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import LLMResult

from app.gateways.regolo.key_store import UserAiKeyStore
from app.gateways.sqlite.repositories import UsageRepo
from app.helpers.logging import get_logger
from app.helpers.settings import Settings
from app.i18n.errors import LocalizedError

log = get_logger("llm")

PREFERRED_MODELS = (
    "gpt-oss-120b",
    "Llama-3.3-70B-Instruct",
    "Qwen3-30B-A3B-Instruct-2507",
    "mistral-small3.2",
    "Llama-3.1-8B-Instruct",
)


def _norm(model_id: str) -> str:
    return "".join(c for c in model_id.lower() if c.isalnum())


def pick_model(wanted: str | None, available: list[str]) -> str | None:
    """Exact-ish match for the configured model, else the preference list, else the first id."""
    if not available:
        return None
    if wanted:
        w = _norm(wanted)
        for mid in available:
            if _norm(mid) == w or _norm(mid.split("/")[-1]) == w:
                return mid
    for pref in PREFERRED_MODELS:
        for mid in available:
            if _norm(pref) in _norm(mid):
                return mid
    return available[0]


class LLMNotConfigured(LocalizedError):
    status = 503


class UsageContext:
    """Who pays for a call: recorded locally per org/user/day/model."""

    def __init__(self, org_id: str, user_id: str):
        self.org_id, self.user_id = org_id, user_id


class LLMProvider(Protocol):
    model_name: str | None

    def chat_model(self, usage: UsageContext | None = None, temperature: float | None = None) -> BaseChatModel: ...

    @property
    def configured(self) -> bool: ...

    def key_for(self, user_id: str | None) -> str | None: ...

    def configured_for(self, user_id: str) -> bool: ...

    async def resolve_model(self, api_key: str | None = None) -> str | None: ...

    async def list_models(self, api_key: str) -> list[str]: ...


class _UsageCallback(BaseCallbackHandler):
    def __init__(self, repo: UsageRepo, usage: UsageContext, model: str):
        self.repo, self.usage, self.model = repo, usage, model

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tin = tout = 0
        meta = response.llm_output or {}
        tu = meta.get("token_usage") or {}
        tin, tout = tu.get("prompt_tokens", 0), tu.get("completion_tokens", 0)
        if not (tin or tout):
            for gens in response.generations:
                for g in gens:
                    um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                    tin += um.get("input_tokens", 0)
                    tout += um.get("output_tokens", 0)
        try:
            self.repo.add(self.usage.org_id, self.usage.user_id, self.model, int(tin), int(tout))
        except Exception as exc:  # usage must never break a call
            log.warning("usage record failed: %s", type(exc).__name__)


class KeyRejected(ValueError):
    pass


class RegoloProvider:
    def __init__(self, settings: Settings, usage_repo: UsageRepo, keys: UserAiKeyStore):
        self.settings = settings
        self.usage_repo = usage_repo
        self.keys = keys
        self.model_name: str | None = settings.regolo_model
        self._model_verified = False

    @property
    def configured(self) -> bool:
        """A developer key is set in .env (organisations may still bring their own)."""
        return bool(self.settings.regolo_api_key)

    def key_for(self, user_id: str | None) -> str | None:
        stored = self.keys.get(user_id)
        return stored.api_key if stored else self.settings.regolo_api_key

    def configured_for(self, user_id: str) -> bool:
        return bool(self.key_for(user_id))

    async def list_models(self, api_key: str) -> list[str]:
        """Also validates a key: 401/403 → KeyRejected; network problems → httpx errors."""
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(
                self.settings.regolo_base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if r.status_code in (401, 403):
            raise KeyRejected("key rejected by Regolo.ai")
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    async def resolve_model(self, api_key: str | None = None) -> str | None:
        """Turn the configured model (e.g. "gpt-oss-120b") into Regolo's exact id.

        Matching ignores case, spaces, dashes and underscores; if the configured model is not
        offered, fall back to PREFERRED_MODELS, then to the first model listed. Called when a
        key is verified and lazily before the first AI call; without network the configured
        name is kept as-is.
        """
        key = api_key or self.settings.regolo_api_key
        if self._model_verified or not key:
            return self.model_name
        try:
            ids = await self.list_models(key)
        except Exception as exc:
            log.warning("could not list Regolo models: %s", type(exc).__name__)
            return self.model_name
        picked = pick_model(self.settings.regolo_model, ids)
        if picked:
            if picked != self.model_name:
                log.info("using Regolo model %s", picked)
            self.model_name, self._model_verified = picked, True
        return self.model_name

    def chat_model(self, usage: UsageContext | None = None, temperature: float | None = None) -> BaseChatModel:
        api_key = self.key_for(usage.user_id if usage else None)
        if not api_key:
            raise LLMNotConfigured("errors.ai_key_missing")
        if not self.model_name:
            raise LLMNotConfigured("errors.ai_model_unreachable")
        from langchain_openai import ChatOpenAI

        callbacks = [_UsageCallback(self.usage_repo, usage, self.model_name)] if usage else []
        return ChatOpenAI(
            model=self.model_name,
            api_key=api_key,
            base_url=self.settings.regolo_base_url,
            temperature=self.settings.llm_temperature if temperature is None else temperature,
            timeout=self.settings.llm_timeout_s,
            max_retries=4,  # unstable factory networks: retry with back-off
            callbacks=callbacks,
            stream_usage=True,
        )
