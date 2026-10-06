"""husk-gateway answers become localized errors. Messages never carry tokens, prompts or document text."""

from __future__ import annotations

from typing import Any

import httpx

from app.i18n.errors import LocalizedError


class GatewayError(LocalizedError):
    status = 503


def message_of(body: Any) -> str:
    """The gateway's OpenAI-style body: {"error": {"message": ..., "code": ...}}."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or "")
    return ""


def error_for(status: int, message: str = "") -> GatewayError:
    m = message.lower()
    if status == 401:
        if "organisation" in m:  # the Clerk user has no active organisation: the token carries no org_id
            return GatewayError("errors.gateway_no_org", status=403)
        return GatewayError("errors.session_expired", status=401)
    if status == 403:
        return GatewayError("errors.gateway_not_entitled", status=403)
    if status == 429:
        return GatewayError("errors.gateway_busy", status=429)
    if status in (400, 404, 422):
        return GatewayError("errors.gateway_rejected", status=502)
    return GatewayError("errors.gateway_unavailable", status=503)


def from_response(resp: httpx.Response) -> GatewayError:
    try:
        message = message_of(resp.json())
    except Exception:
        message = ""
    return error_for(resp.status_code, message)


def from_exception(exc: BaseException) -> LocalizedError | None:
    """Map what the OpenAI SDK / httpx raise on a gateway call; None = not a gateway problem."""
    if isinstance(exc, LocalizedError):
        return exc
    try:
        import openai
    except ImportError:  # pragma: no cover
        return None
    if isinstance(exc, openai.APIStatusError):
        return error_for(exc.status_code, message_of(getattr(exc, "body", None)) or str(exc))
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return GatewayError("errors.gateway_unavailable", status=503)
    return None


def raise_if_gateway(exc: BaseException) -> None:
    """Call in an `except` block: a gateway problem (not entitled, signed out, busy, down) is raised as a localized
    error at once instead of being retried or wrapped by the SDK; anything else returns and is handled as before."""
    mapped = from_exception(exc)
    if isinstance(mapped, GatewayError):
        raise mapped from exc
