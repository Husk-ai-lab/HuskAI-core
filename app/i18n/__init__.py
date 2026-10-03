"""Internationalisation: one shared message catalogue for backend and frontend.

    app/i18n/locales/<lang>.json   flat, dot-namespaced keys ("errors.not_found")

* Interpolation: "{name}" placeholders, filled from keyword params.
* Plurals: "key_one" / "key_other", chosen from the `count` param (i18next convention).
* Fallback: requested language → DEFAULT_LANGUAGE → the key itself.
* Params given as {"en": ..., "it": ...} dicts resolve to the target language (used for
  content that already comes translated, e.g. Neon maintenance messages).

Add a language: create locales/<code>.json with the same keys and add the code to
SUPPORTED_LANGUAGES in app/config.py. test/test_i18n.py checks key and placeholder parity.
"""

from __future__ import annotations

import json
import string
from functools import cache
from pathlib import Path
from typing import Any

from app.config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.helpers.settings import resource_root

LANGUAGE_NAMES = {"en": "English", "it": "Italian"}  # how agents are told which language to write
LOCALES = {"en": "en-GB", "it": "it-IT"}


def _locales_dir() -> Path:
    bundled = resource_root() / "app" / "i18n" / "locales"
    return bundled if bundled.exists() else Path(__file__).parent / "locales"


@cache
def catalogue(lang: str) -> dict[str, str]:
    path = _locales_dir() / f"{lang}.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def normalize(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANGUAGE
    code = lang.strip().lower().replace("_", "-").split("-")[0]
    return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def resolve_lang(accept_language: str | None) -> str:
    """First supported tag of an Accept-Language header (quality values ignored), else default."""
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower().split("-")[0]
        if code in SUPPORTED_LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def _plural_suffix(lang: str, count: Any) -> str:
    try:
        n = float(count)
    except (TypeError, ValueError):
        return "other"
    return "one" if n == 1 else "other"  # en, it (CLDR); extend per language when needed


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _lookup(key: str, lang: str) -> str | None:
    for code in (lang, DEFAULT_LANGUAGE):
        value = catalogue(code).get(key)
        if value is not None:
            return value
    return None


def t(key: str, lang: str | None = None, **params: Any) -> str:
    lang = normalize(lang)
    template = None
    if "count" in params:
        template = _lookup(f"{key}_{_plural_suffix(lang, params['count'])}", lang)
    if template is None:
        template = _lookup(key, lang)
    if template is None:
        return key
    resolved = {k: localized_value(v, lang) for k, v in params.items()}
    return string.Formatter().vformat(template, (), _Safe(resolved))


def localized_value(value: Any, lang: str) -> Any:
    """{"en": "...", "it": "..."} → the string for `lang` (fallback default, then any)."""
    if isinstance(value, dict) and value and all(k in LANGUAGE_NAMES or len(k) == 2 for k in value):
        return value.get(lang) or value.get(DEFAULT_LANGUAGE) or next((v for v in value.values() if v), "")
    return value


def language_name(lang: str | None) -> str:
    return LANGUAGE_NAMES.get(normalize(lang), LANGUAGE_NAMES[DEFAULT_LANGUAGE])
