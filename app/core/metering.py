"""Token metering per step and per model.

A `Meter` collects `{model: {calls, tokens_in, tokens_out}}`. `metered(meter)` makes it current for
everything awaited inside (context variables follow asyncio tasks), and `MeterCallback`, attached by
the LLM provider to every chat model, adds each call to the current meter(s). Nesting is allowed:
a run-level meter (benchmarks, budgets) and a step-level meter both receive every call.

A meter may carry a token `budget`: once reached, the next call raises `BudgetExceeded`
before anything is sent (used by the live benchmark).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Meter:
    budget: int | None = None  # total tokens (in + out); None = unlimited
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, model: str, tokens_in: int, tokens_out: int) -> None:
        m = self.by_model.setdefault(model, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
        m["calls"] += 1
        m["tokens_in"] += int(tokens_in)
        m["tokens_out"] += int(tokens_out)

    @property
    def calls(self) -> int:
        return sum(m["calls"] for m in self.by_model.values())

    @property
    def tokens(self) -> int:
        return sum(m["tokens_in"] + m["tokens_out"] for m in self.by_model.values())

    def over_budget(self) -> bool:
        return self.budget is not None and self.tokens >= self.budget

    def as_dict(self) -> dict[str, Any]:
        total = {"calls": self.calls,
                 "tokens_in": sum(m["tokens_in"] for m in self.by_model.values()),
                 "tokens_out": sum(m["tokens_out"] for m in self.by_model.values())}
        return {"by_model": {k: dict(v) for k, v in self.by_model.items()}, "total": total}


_current: ContextVar[tuple[Meter, ...]] = ContextVar("husk_meters", default=())


@contextmanager
def metered(meter: Meter) -> Iterator[Meter]:
    token = _current.set(_current.get() + (meter,))
    try:
        yield meter
    finally:
        _current.reset(token)


def current_meters() -> tuple[Meter, ...]:
    return _current.get()


def usage_from(response: LLMResult) -> tuple[int, int]:
    """Prompt/completion tokens as reported by the provider (OpenAI format or usage_metadata)."""
    tu = (response.llm_output or {}).get("token_usage") or {}
    tin, tout = tu.get("prompt_tokens", 0) or 0, tu.get("completion_tokens", 0) or 0
    if not (tin or tout):
        for gens in response.generations:
            for g in gens:
                um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                tin += um.get("input_tokens", 0)
                tout += um.get("output_tokens", 0)
    return int(tin), int(tout)


class MeterCallback(BaseCallbackHandler):
    """Captures the meters current when the chat model is created (robust to callback threads)."""

    raise_error = True  # BudgetExceeded must stop the call

    def __init__(self, model: str):
        self.model = model
        self.meters = current_meters()

    def _check(self) -> None:
        for m in self.meters:
            if m.over_budget():
                raise BudgetExceeded(f"token budget reached ({m.tokens} ≥ {m.budget})")

    def on_chat_model_start(self, serialized: dict, messages: list, **kwargs: Any) -> None:
        self._check()

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        self._check()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tin, tout = usage_from(response)
        for m in self.meters:
            m.add(self.model, tin, tout)
