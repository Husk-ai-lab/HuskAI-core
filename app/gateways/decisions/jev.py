"""Venice AI's Jev (`jev-latest`, beta) over `POST /api/v1/decisions`.

* state + many typed questions per request, answered in parallel; large question sets are split into
  batches sent concurrently;
* the caller's key (the Venice part of the combined key), or `VENICE_API_KEY` in .env for development;
* usage is metered per step/model (`app/core/metering`) and per org/user/day (`token_usage`);
* state text is never logged (GDPR).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.metering import BudgetExceeded, current_meters
from app.gateways.decisions.base import Answer, DecisionUnavailable, Question
from app.gateways.regolo.key_store import UserAiKeyStore
from app.gateways.sqlite.repositories import UsageRepo
from app.helpers.logging import get_logger
from app.helpers.settings import Settings

log = get_logger("decisions")

BATCH = 64
TIMEOUT_S = 20.0


class VeniceKeyRejected(ValueError):
    pass


class JevClient:
    def __init__(self, settings: Settings, keys: UserAiKeyStore, usage_repo: UsageRepo | None = None):
        self.settings, self.keys, self.usage_repo = settings, keys, usage_repo
        self.model_name = settings.decision_model

    def key_for(self, user_id: str | None) -> str | None:
        stored = self.keys.get(user_id)
        return (stored.decision_key if stored and stored.decision_key else None) or self.settings.venice_api_key

    def configured_for(self, user_id: str) -> bool:
        return bool(self.key_for(user_id))

    async def verify(self, key: str) -> None:
        """Free call: 401/403 → VeniceKeyRejected; network problems → httpx errors."""
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(self.settings.venice_base_url.rstrip("/") + "/models",
                               headers={"Authorization": f"Bearer {key}"})
        if r.status_code in (401, 403):
            raise VeniceKeyRejected("key rejected by Venice AI")
        r.raise_for_status()

    async def ask(self, user_id: str, org_id: str, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        key = self.key_for(user_id)
        if not key:
            raise DecisionUnavailable("errors.ai_key_venice_missing")
        items = list(questions.items())
        batches = [dict(items[i:i + BATCH]) for i in range(0, len(items), BATCH)] or [{}]
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
            parts = await asyncio.gather(*(self._one(http, key, user_id, org_id, state, b) for b in batches if b))
        out: dict[str, Answer] = {}
        for p in parts:
            out.update(p)
        return out

    async def _one(self, http: httpx.AsyncClient, key: str, user_id: str, org_id: str, state: str,
                   questions: dict[str, Question]) -> dict[str, Answer]:
        meters = current_meters()
        for m in meters:
            if m.over_budget():
                raise BudgetExceeded(f"token budget reached ({m.tokens} ≥ {m.budget})")
        body = {"model": self.model_name, "state": state,
                "questions": {k: q.model_dump(exclude_none=True) for k, q in questions.items()}}
        last: Exception | None = None
        for attempt in range(2):  # one retry (network, 429, 5xx)
            try:
                r = await http.post(self.settings.venice_base_url.rstrip("/") + "/decisions", json=body,
                                    headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                last = exc
                continue
            if r.status_code == 402:
                raise DecisionUnavailable("errors.decisions_quota")
            if r.status_code in (401, 403):
                raise DecisionUnavailable("errors.ai_key_venice_rejected")
            if r.status_code in (429, 500, 502, 503) and attempt == 0:
                await asyncio.sleep(1.0)
                last = RuntimeError(f"Venice {r.status_code}")
                continue
            if r.status_code >= 400:
                raise DecisionUnavailable("errors.decisions_failed", detail=f"HTTP {r.status_code}")
            data = r.json()
            self._meter(meters, user_id, org_id, data.get("usage") or {})
            return {k: _answer(v) for k, v in (data.get("answers") or {}).items()}
        log.warning("decision request failed: %s", type(last).__name__ if last else "unknown")
        raise DecisionUnavailable("errors.decisions_failed", detail=type(last).__name__ if last else "")

    def _meter(self, meters: tuple, user_id: str, org_id: str, usage: dict[str, Any]) -> None:
        tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        for m in meters:
            m.add(self.model_name, tin, tout)
        if self.usage_repo:
            try:
                self.usage_repo.add(org_id, user_id, self.model_name, tin, tout)
            except Exception as exc:  # usage must never break a decision
                log.warning("usage record failed: %s", type(exc).__name__)


def _answer(v: dict[str, Any]) -> Answer:
    t = v.get("type", "noul")
    if t == "noul":
        return Answer(type="noul", p=float(v.get("noul", 0.0)))
    if t == "choice":
        return Answer(type="choice", choice=v.get("choice"), probabilities=v.get("probabilities") or {},
                      confidence=v.get("confidence"))
    return Answer(type="score", score=float(v.get("score", 0.0)), probabilities=v.get("probabilities") or {},
                  confidence=v.get("confidence"))
