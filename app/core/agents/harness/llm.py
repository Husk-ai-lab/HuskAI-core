"""LLM helpers on top of LangChain: structured outputs with a robust fallback."""

from __future__ import annotations

from typing import TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.helpers.logging import get_logger
from app.helpers.text import extract_json

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    pass


async def structured(
    model: BaseChatModel, schema: type[T], messages: list[BaseMessage], config: dict | None = None
) -> T:
    """Ask for a typed answer. Tries native structured output, then strict JSON prompting."""
    try:
        runnable = model.with_structured_output(schema, method="function_calling")
        result = await runnable.ainvoke(messages, config=config)
        if isinstance(result, schema):
            return result
        if isinstance(result, dict):
            return schema.model_validate(result)
    except Exception as exc:
        log.debug("native structured output failed (%s), falling back to JSON", type(exc).__name__)
    schema_json = schema.model_json_schema()
    fallback = list(messages) + [
        HumanMessage(
            content=(
                "Reply ONLY with a valid JSON object that follows this JSON Schema, with no text "
                f"before or after:\n{schema_json}"
            )
        )
    ]
    last_err: Exception | None = None
    for _ in range(2):
        msg = await model.ainvoke(fallback, config=config)
        try:
            return schema.model_validate(extract_json(str(msg.content)))
        except (ValidationError, ValueError) as exc:
            last_err = exc
            fallback = fallback + [msg, HumanMessage(content=f"Invalid JSON: {str(exc)[:500]}. Fix it.")]
    raise StructuredOutputError(f"invalid structured answer: {last_err}")


def system(text: str) -> SystemMessage:
    return SystemMessage(content=text)


def human(text: str) -> HumanMessage:
    return HumanMessage(content=text)
