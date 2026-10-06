"""Direct providers for BENCHMARKS ONLY (live runs on OpenRouter / Regolo.ai / Venice with keys from `.env`).

The product never does this: all its AI goes through husk-gateway with the user's Clerk token and no AI key
exists on a customer's computer. These classes implement the same interfaces as the gateway ones
(`LLMProvider`, `DecisionModel`) so the pipeline under test is exactly the product's."""

from __future__ import annotations

import asyncio
from pathlib import Path  # noqa: E402
from typing import Any

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.metering import BudgetExceeded, MeterCallback, current_meters
from app.gateways.decisions.base import Answer, DecisionUnavailable, Question
from app.gateways.gateway.llm import UsageContext, _UsageCallback
from app.gateways.sqlite.repositories import UsageRepo
from app.helpers.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
VENICE_BASE_URL = "https://api.venice.ai/api/v1"
VENICE_BATCH = 64


class DirectProvider:
    """OpenAI-compatible endpoint called directly with one key (OpenRouter or Regolo.ai)."""

    def __init__(self, settings: Settings, usage_repo: UsageRepo, *, api_key: str, base_url: str, model: str,
                 summary_model: str | None):
        self.settings, self.usage_repo = settings, usage_repo
        self.api_key, self.base_url, self.model_name, self._summary = api_key, base_url, model, summary_model

    @property
    def summary_model_name(self) -> str | None:
        return self._summary

    def configured_for(self, user_id: str) -> bool:
        return True

    def chat_model(self, usage: UsageContext | None = None, temperature: float | None = None,
                   model: str | None = None, reasoning: str | None = None, max_tokens: int | None = None,
                   timeout: float | None = None, max_retries: int | None = None) -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        model_id = model or self.model_name
        callbacks: list[BaseCallbackHandler] = [MeterCallback(model_id)]
        if usage:
            callbacks.append(_UsageCallback(self.usage_repo, usage, model_id))
        extra: dict[str, Any] = {}
        if "openrouter.ai" in self.base_url:
            body: dict[str, Any] = {"provider": {"sort": "throughput"}}  # route to the fastest backends
            if reasoning:
                body["reasoning"] = {"effort": reasoning}
            extra["extra_body"] = body
        elif reasoning:
            extra["reasoning_effort"] = reasoning
        if max_tokens:
            extra["max_tokens"] = max_tokens
        return ChatOpenAI(model=model_id, api_key=self.api_key, base_url=self.base_url,
                          temperature=self.settings.llm_temperature if temperature is None else temperature,
                          timeout=timeout or self.settings.llm_timeout_s,
                          max_retries=4 if max_retries is None else max_retries,
                          callbacks=callbacks, stream_usage=True, **extra)


class DirectJev:
    """Venice AI's Jev over `POST /api/v1/decisions` (the pre-gateway wire format), key from `.env`."""

    def __init__(self, settings: Settings, usage_repo: UsageRepo | None, api_key: str):
        self.settings, self.usage_repo, self.api_key = settings, usage_repo, api_key
        self.model_name = "jev-latest"

    def configured_for(self, user_id: str) -> bool:
        return True

    async def ask(self, user_id: str, org_id: str, state: str, questions: dict[str, Question],
                  lang: str = "it") -> dict[str, Answer]:
        items = list(questions.items())
        batches = [dict(items[i:i + VENICE_BATCH]) for i in range(0, len(items), VENICE_BATCH)]
        async with httpx.AsyncClient(timeout=20.0) as http:
            parts = await asyncio.gather(*(self._one(http, user_id, org_id, state, b) for b in batches if b))
        out: dict[str, Answer] = {}
        for p in parts:
            out.update(p)
        return out

    async def _one(self, http: httpx.AsyncClient, user_id: str, org_id: str, state: str,
                   questions: dict[str, Question]) -> dict[str, Answer]:
        meters = current_meters()
        for m in meters:
            if m.over_budget():
                raise BudgetExceeded(f"token budget reached ({m.tokens} ≥ {m.budget})")
        body = {"model": self.model_name, "state": state,
                "questions": {k: q.model_dump(exclude_none=True) for k, q in questions.items()}}
        last: Exception | None = None
        for attempt in range(2):
            try:
                r = await http.post(VENICE_BASE_URL + "/decisions", json=body,
                                    headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as exc:
                last = exc
                continue
            if r.status_code in (429, 500, 502, 503) and attempt == 0:
                await asyncio.sleep(1.0)
                last = RuntimeError(f"Venice {r.status_code}")
                continue
            if r.status_code >= 400:
                raise DecisionUnavailable("errors.gateway_unavailable", status=503)
            data = r.json()
            usage = data.get("usage") or {}
            tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            for m in meters:
                m.add(self.model_name, tin, tout)
            if self.usage_repo:
                self.usage_repo.add(org_id, user_id, self.model_name, tin, tout)
            return {k: _answer(v) for k, v in (data.get("answers") or {}).items()}
        raise DecisionUnavailable("errors.gateway_unavailable", status=503) from last


def _answer(v: dict[str, Any]) -> Answer:
    t = v.get("type", "noul")
    if t == "noul":
        return Answer(type="noul", p=float(v.get("noul", 0.0)))
    if t == "choice":
        return Answer(type="choice", choice=v.get("choice"), probabilities=v.get("probabilities") or {},
                      confidence=v.get("confidence"))
    return Answer(type="score", score=float(v.get("score", 0.0)), probabilities=v.get("probabilities") or {},
                  confidence=v.get("confidence"))


def env_value(name: str) -> str | None:
    """A key from the git-ignored `.env` (benchmarks only)."""
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def live_services(svc, settings: Settings, *, key_name: str, base_url: str, model: str, summary_model: str | None) -> None:  # type: ignore[no-untyped-def]
    """Swap the gateway-backed providers of `svc` for direct ones (keys from `.env`)."""
    key = env_value(key_name)
    if not key:
        raise SystemExit(f"{key_name} is missing from .env")
    venice = env_value("VENICE_API_KEY")
    if not venice:
        raise SystemExit("VENICE_API_KEY is missing from .env (decision model)")
    svc.llm = DirectProvider(settings, UsageRepo(svc.db), api_key=key, base_url=base_url, model=model, summary_model=summary_model)
    svc.decisions = DirectJev(settings, UsageRepo(svc.db), venice)
