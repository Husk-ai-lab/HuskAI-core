"""The decision model behind husk-gateway (`POST /v1/decide`): Venice today (Jev when it is deployed), Serenity later.

Discovery keeps its own typed questions (`gateways/decisions/base.py`: `noul` / `choice` / `score` with
`criteria`); this module is the adapter to the gateway's wire format (`choice` / `score`, typed by id):

* `noul`   → `choice` over its two criteria texts; `Answer.p` = probability of the "true" option;
* `choice` → `choice` over the criteria descriptions, mapped back to the criteria keys;
* `score`  → the gateway's `score` when it has the 10 bands the gateway calibrates for, else a `choice` over the
  level labels (e.g. the 8 hour buckets); either way `Answer.probabilities` is keyed by level index ("0", "1", …),
  which is what the Explorer's expected-value maths reads.

Limits of the gateway handled here: 16 questions per request, `state` of at most 12 000 characters.
The state text is never logged (GDPR)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.core.metering import BudgetExceeded, current_meters
from app.gateways.decisions.base import Answer, DecisionUnavailable, Question
from app.gateways.gateway.client import GatewayClient
from app.gateways.gateway.errors import GatewayError
from app.gateways.sqlite.repositories import UsageRepo
from app.helpers.logging import get_logger
from app.helpers.settings import Settings

log = get_logger("decisions")

BATCH = 16  # gateway: DECISION_MAX_QUESTIONS
STATE_MAX = 12_000  # gateway: state is 1–12000 characters
TEXT_MAX = 500  # gateway: question wording is at most 500 characters
SCORE_BANDS = 10  # the gateway's `score` type answers in 10 bands

Decoder = Callable[[dict[str, Any]], Answer]


def fit_state(state: str, limit: int = STATE_MAX) -> str:
    """Make the state fit: short lines (aliases, titles) are kept whole, the long ones (document summaries) share
    what is left of the budget equally."""
    state = state.strip() or "-"
    if len(state) <= limit:
        return state
    lines = state.splitlines()
    fixed = sum(len(ln) + 1 for ln in lines if len(ln) <= 80)
    long_n = sum(1 for ln in lines if len(ln) > 80)
    share = max(80, (limit - fixed) // max(1, long_n) - 2)
    out = "\n".join(ln if len(ln) <= 80 else ln[: share - 1].rstrip() + "…" for ln in lines)
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _unique(options: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for o in options:
        n = seen.get(o, 0)
        seen[o] = n + 1
        out.append(o if n == 0 else f"{o} ({n + 1})")
    return out


def _text(q: Question) -> str:
    return q.instructions[:TEXT_MAX]


def to_wire(qid: str, q: Question) -> tuple[dict[str, Any], Decoder]:
    """One app question → (gateway question, decoder of the gateway's answer into an `Answer`)."""
    if q.type == "noul":
        crit = q.criteria if isinstance(q.criteria, dict) else {}
        yes = (crit.get("true") or "yes")[:120]
        no = (crit.get("false") or "no")[:120]
        if yes == no:
            no = "no" if yes != "no" else "not " + yes
        wire = {"id": qid, "type": "choice", "options": [yes, no], "text": _text(q)}

        def decode_noul(raw: dict[str, Any]) -> Answer:
            probs = raw.get("probabilities") or {}
            if raw.get("p") is None and not probs:
                return Answer(type="noul", p=None)
            p = probs.get(yes)
            if p is None:
                p = raw.get("p") if raw.get("answer") == yes else 1.0 - float(raw.get("p") or 0.0)
            return Answer(type="noul", p=float(p), probabilities={"true": float(p), "false": 1.0 - float(p)})

        return wire, decode_noul

    if q.type == "choice":
        crit = q.criteria if isinstance(q.criteria, dict) else {}
        keys = list(crit)
        options = _unique([(crit[k] or k)[:120] for k in keys])
        wire = {"id": qid, "type": "choice", "options": options, "text": _text(q)}

        def decode_choice(raw: dict[str, Any]) -> Answer:
            probs = raw.get("probabilities") or {}
            by_key = {k: float(probs.get(o, 0.0)) for k, o in zip(keys, options, strict=True)}
            answer = raw.get("answer")
            choice = next((k for k, o in zip(keys, options, strict=True) if o == answer), None)
            conf = raw.get("p")
            return Answer(type="choice", choice=choice, probabilities=by_key, confidence=None if conf is None else float(conf))

        return wire, decode_choice

    levels = [str(x) for x in (q.criteria if isinstance(q.criteria, list) else [])]
    if len(levels) == SCORE_BANDS:
        wire = {"id": qid, "type": "score", "text": _text(q)}

        def decode_score(raw: dict[str, Any]) -> Answer:
            probs = raw.get("probabilities")
            by_level = {str(i): float(p) for i, p in enumerate(probs)} if isinstance(probs, list) else {}
            value = float(raw.get("answer") or 0.0)
            return Answer(type="score", score=value * (SCORE_BANDS - 1), probabilities=by_level)

        return wire, decode_score

    options = _unique([lv[:120] for lv in levels])
    wire = {"id": qid, "type": "choice", "options": options, "text": _text(q)}

    def decode_levels(raw: dict[str, Any]) -> Answer:
        probs = raw.get("probabilities") or {}
        by_level = {str(i): float(probs.get(o, 0.0)) for i, o in enumerate(options)}
        pos = sum(i * p for i, p in enumerate(by_level.values())) / (sum(by_level.values()) or 1.0)
        return Answer(type="score", score=pos, probabilities=by_level)

    return wire, decode_levels


class GatewayDecisionModel:
    def __init__(self, settings: Settings, gateway: GatewayClient, usage_repo: UsageRepo | None = None):
        self.settings, self.gateway, self.usage_repo = settings, gateway, usage_repo
        self.model_name = settings.decision_model

    def configured_for(self, user_id: str) -> bool:
        return self.gateway.signed_in(user_id)

    async def ask(self, user_id: str, org_id: str, state: str, questions: dict[str, Question],
                  lang: str = "it") -> dict[str, Answer]:
        if not self.configured_for(user_id):
            raise DecisionUnavailable("errors.session_expired", status=401)
        wired = [to_wire(qid, q) for qid, q in questions.items()]
        batches = [wired[i:i + BATCH] for i in range(0, len(wired), BATCH)]
        state = fit_state(state)
        parts = await asyncio.gather(*(self._one(user_id, org_id, state, lang, b) for b in batches))
        out: dict[str, Answer] = {}
        for p in parts:
            out.update(p)
        return out

    async def _one(self, user_id: str, org_id: str, state: str, lang: str,
                   batch: list[tuple[dict[str, Any], Decoder]]) -> dict[str, Answer]:
        meters = current_meters()
        for m in meters:
            if m.over_budget():
                raise BudgetExceeded(f"token budget reached ({m.tokens} ≥ {m.budget})")
        body: dict[str, Any] = {"state": state, "lang": "en" if lang == "en" else "it", "questions": [w for w, _ in batch]}
        if self.model_name:
            body["model"] = self.model_name
        try:
            r = await self.gateway._call("POST", "/v1/decide", user_id, timeout=30.0, json=body)
        except GatewayError as exc:
            raise DecisionUnavailable(exc.key, status=exc.status) from exc
        data = r.json()
        self._meter(meters, user_id, org_id, len(state) + sum(len(w["text"]) for w, _ in batch), len(batch))
        return {w["id"]: decode(data[w["id"]]) for w, decode in batch if isinstance(data.get(w["id"]), dict)}

    def _meter(self, meters: tuple, user_id: str, org_id: str, chars: int, n: int) -> None:
        """The gateway reports no token counts for decisions (it meters them itself): estimate for the local report
        — about 4 characters per token in, one token out per question (single-token forward pass)."""
        tin, tout = chars // 4, n
        for m in meters:
            m.add(self.model_name, tin, tout)
        if self.usage_repo:
            try:
                self.usage_repo.add(org_id, user_id, self.model_name, tin, tout)
            except Exception as exc:  # usage must never break a decision
                log.warning("usage record failed: %s", type(exc).__name__)
