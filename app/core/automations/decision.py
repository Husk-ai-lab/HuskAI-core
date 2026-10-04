"""Decision nodes: typed answers with a probability.

Served by the LLM now; Serenity (`/v1/serenity/decide`) replaces it later behind the same
interface — one forward pass, calibrated probabilities, no parsing.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from app.core.agents.harness.context import AgentContext
from app.core.agents.harness.llm import human, structured, system


class Decision(BaseModel):
    answer: str
    probability: float = Field(..., ge=0, le=1)
    reason: str = ""


class DecisionProvider(Protocol):
    async def decide(self, state: str, question: str, options: list[str]) -> Decision: ...


class _Choice(BaseModel):
    answer: str = Field(..., description="exactly one of the options")
    confidence: float = Field(..., ge=0, le=1)
    reason: str = Field("", max_length=300)


class LLMDecisionProvider:
    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    async def decide(self, state: str, question: str, options: list[str]) -> Decision:
        msgs = [
            system("Make a typed decision. Answer with ONE of the given options, never invent others. "
                   "Write the reason in " + self.ctx.language_name + "."),
            human(f"STATE:\n{state[:6000]}\n\nQUESTION: {question}\nOPTIONS: {', '.join(options)}"),
        ]
        out = await structured(self.ctx.model(temperature=0, role="runtime"), _Choice, msgs, config={"run_name": "automation.decision"})
        answer = out.answer.strip()
        if answer not in options:
            lowered = {o.lower(): o for o in options}
            answer = lowered.get(answer.lower(), options[0])
        return Decision(answer=answer, probability=out.confidence, reason=out.reason)
