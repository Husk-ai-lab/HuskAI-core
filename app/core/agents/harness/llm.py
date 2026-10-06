"""LLM helpers on top of LangChain: structured outputs with a robust fallback."""

from __future__ import annotations

from typing import TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.gateways.gateway.errors import raise_if_gateway
from app.helpers.logging import get_logger
from app.helpers.text import extract_json

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    pass


async def structured(
    model: BaseChatModel, schema: type[T], messages: list[BaseMessage], config: dict | None = None,
    prefer: str = "function_calling",
) -> T:
    """Ask for a typed answer: the preferred native mode, then the other one (OpenAI-compatible APIs), then strict
    JSON prompting with up to two corrections."""
    # `prefer="json_schema"` only for small, flat schemas (Explorer, Planner, summaries): on large nested schemas
    # (automation drafts) gpt-oss can run away in strict JSON mode until the length limit. Measured, see docs.
    if type(model).__name__ == "ChatOpenAI":
        methods = ["json_schema", "function_calling"] if prefer == "json_schema" else ["function_calling", "json_schema"]
    else:
        methods = ["function_calling"]
    for method in methods:
        try:
            runnable = model.with_structured_output(schema, method=method)
            result = await runnable.ainvoke(messages, config=config)
            if isinstance(result, schema):
                return result
            if isinstance(result, dict):
                return schema.model_validate(result)
        except Exception as exc:
            raise_if_gateway(exc)  # not entitled / signed out / gateway down: no point trying other modes
            log.debug("structured output via %s failed (%s)", method, type(exc).__name__)
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
        try:
            msg = await model.ainvoke(fallback, config=config)
        except Exception as exc:
            raise_if_gateway(exc)
            raise
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
