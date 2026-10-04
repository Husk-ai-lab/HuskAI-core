"""The decision-model interface discovery depends on. Any System 1 model (Venice Jev today, Husk AI's
Serenity tomorrow) implements `DecisionModel.ask`; discovery code never sees the provider."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.i18n.errors import LocalizedError


class Question(BaseModel):
    """noul: yes/no (criteria {"true": …, "false": …}); choice: pick one (criteria {option: description});
    score: ordered levels, lowest first (criteria [level, …])."""

    type: Literal["noul", "choice", "score"]
    instructions: str
    criteria: dict[str, str | None] | list[str] | None = None


class Answer(BaseModel):
    type: Literal["noul", "choice", "score"]
    p: float | None = None  # noul: probability of yes
    choice: str | None = None
    score: float | None = None  # score: expected level position (0 = lowest)
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None


class DecisionUnavailable(LocalizedError):
    status = 503


class DecisionModel(Protocol):
    model_name: str

    def configured_for(self, user_id: str) -> bool: ...

    async def ask(self, user_id: str, org_id: str, state: str, questions: dict[str, Question]) -> dict[str, Answer]: ...
