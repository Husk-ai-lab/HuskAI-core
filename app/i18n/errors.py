"""Errors that carry a message key instead of text, translated per request."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException, Request

from app.i18n import resolve_lang, t


class LocalizedError(Exception):
    """Domain error with a message key; `str(exc)` is the English text (logs, agent tools)."""

    status: int = 400

    def __init__(self, key: str, status: int | None = None, **params: Any):
        self.key = key
        self.params = params
        if status is not None:
            self.status = status
        super().__init__(t(key, "en", **params))


def api_error(status: int, key: str, **params: Any) -> NoReturn:
    """Raise an HTTP error whose detail is rendered in the caller's language by the handler."""
    raise HTTPException(status_code=status, detail={"code": key, "params": params})


def from_localized(exc: LocalizedError, status: int | None = None) -> NoReturn:
    api_error(status or exc.status, exc.key, **exc.params)


def request_lang(request: Request) -> str:
    return resolve_lang(request.headers.get("accept-language"))


def render(request: Request, key: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return {"detail": t(key, request_lang(request), **params), "code": key, "params": _plain(params)}


def _plain(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if isinstance(v, (str, int, float, bool)) or v is None}
