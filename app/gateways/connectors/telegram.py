"""Telegram Bot API (https://core.telegram.org/bots/api). The customer's own bot (created with @BotFather):
messages go from this computer straight to Telegram, not through Husk's servers.

The bot token sits in every request URL, so nothing here ever logs, stores or re-raises an httpx exception or a URL:
failures become a `ConnectorError` with a short code."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from app.gateways.clerk.oidc import USER_AGENT
from app.gateways.connectors.errors import ConnectorError

API = "https://api.telegram.org"
MAX_TEXT = 4096  # sendMessage: 1–4096 characters
PACE_S = 1.1  # at most one message per second to the same chat
MAX_FILE = 20 * 1024 * 1024
MAX_RETRY_AFTER_S = 30


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Chunks of at most `limit` characters, cut at line breaks (or spaces) where possible."""
    text = text.strip() or "-"
    out: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    out.append(text)
    return out


async def _call(http: httpx.AsyncClient, token: str, method: str, *, retry: bool = True, **kw: Any) -> Any:
    url = f"{API}/bot{token}/{method}"
    try:
        r = await http.post(url, timeout=20, headers={"User-Agent": USER_AGENT}, **kw)
    except httpx.HTTPError:
        raise ConnectorError("unreachable") from None  # the exception text carries the URL, i.e. the token
    try:
        data = r.json()
    except ValueError:
        data = {}
    if data.get("ok"):
        return data.get("result")
    desc = str(data.get("description") or "").lower()
    if r.status_code in (401, 404):
        raise ConnectorError("invalid_token")
    if r.status_code == 429:
        wait = int((data.get("parameters") or {}).get("retry_after") or 1)
        if retry and wait <= MAX_RETRY_AFTER_S:
            await asyncio.sleep(wait + 0.5)
            return await _call(http, token, method, retry=False, **kw)
        raise ConnectorError("rate_limited")
    if r.status_code == 409 or "webhook" in desc:
        raise ConnectorError("webhook_active")
    if r.status_code in (400, 403) and any(s in desc for s in ("chat not found", "blocked", "kicked", "not a member", "rights")):
        raise ConnectorError("chat_not_found")
    raise ConnectorError("failed")


async def verify(http: httpx.AsyncClient, token: str) -> str:
    """The token is valid: returns the bot's @username (public, shown to the user)."""
    me = await _call(http, token, "getMe")
    return "@" + str((me or {}).get("username") or "bot")


async def detect_chat(http: httpx.AsyncClient, token: str) -> dict[str, str | None]:
    """The most recent chat that wrote to the bot (the user pressed Start, or wrote in a group the bot is in)."""
    updates = await _call(http, token, "getUpdates", json={"limit": 50, "timeout": 0, "allowed_updates": ["message"]})
    for upd in reversed(updates or []):
        chat = (upd.get("message") or {}).get("chat") or {}
        if chat.get("id") is not None and chat.get("type") in ("private", "group", "supergroup"):
            label = chat.get("first_name") or chat.get("title")
            return {"chat_id": str(chat["id"]), "label": str(label) if label else None}
    raise ConnectorError("no_chat")


async def send(http: httpx.AsyncClient, token: str, chat_id: str, text: str, document: Path | None = None) -> int:
    """Sends the text (split in as many messages as needed) and optionally a file. Returns the messages sent."""
    sent = 0
    for chunk in split_text(text):
        if sent:
            await asyncio.sleep(PACE_S)
        await _call(http, token, "sendMessage", json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True})
        sent += 1
    if document is not None:
        if document.stat().st_size > MAX_FILE:
            raise ConnectorError("attachment_too_large")
        await asyncio.sleep(PACE_S)
        await _call(http, token, "sendDocument", data={"chat_id": chat_id},
                    files={"document": (document.name, document.read_bytes())})
        sent += 1
    return sent
