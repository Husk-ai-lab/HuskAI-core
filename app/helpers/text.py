"""Small text utilities shared by knowledge, memory and agents."""

from __future__ import annotations

import json
import re

_SENTENCE_END = re.compile(r"[.!?…:)\]»\"']\s*$")


def ends_cleanly(text: str) -> bool:
    """True when text ends like a finished sentence (used by the note validator)."""
    return bool(_SENTENCE_END.search(text.strip()))


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # cut on the last sentence boundary when possible, so context never holds half a fact
    idx = max(cut.rfind(". "), cut.rfind("\n"))
    return (cut[: idx + 1] if idx > limit * 0.6 else cut).rstrip() + " […]"


def extract_json(text: str) -> object:
    """Pull the first JSON object/array out of a model answer (fallback path)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return json.loads(text)


def slug(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len] or "item"
