"""Builder personalities: one selector, one prompt fragment each.

Prompts are English instructions; labels and descriptions shown to users live in app/i18n
(`personalities.<id>.label|description`). Add a personality = one entry here + its i18n keys.
"""

from __future__ import annotations

from app.data.models.agent import Personality
from app.data.models.user import PersonalityId
from app.i18n import t

_PROMPTS: dict[PersonalityId, tuple[str, bool]] = {
    PersonalityId.bilanciato: (
        "Style: balanced. Apply clear changes directly. If the request is ambiguous ask ONE focused question. "
        "After each change summarise what changed in 1-3 sentences.",
        False,
    ),
    PersonalityId.ultra_prudente: (
        "Style: ultra-careful. Every change to the flow becomes a PROPOSAL the user must confirm. Before proposing, "
        "check the documents. Always list the risks (missing data, departments involved, effects on the ERP). If "
        "information is missing, ask for it instead of assuming.",
        True,
    ),
    PersonalityId.esplicativo: (
        "Style: explanatory. For every change explain why, in plain language, citing the company document it comes "
        "from. Use concrete examples from the documents. No jargon without explaining it.",
        False,
    ),
    PersonalityId.conciso: ("Style: concise. Answer in at most 2 sentences. No preamble.", False),
}


def get_personality(pid: PersonalityId | str | None, lang: str = "en") -> Personality:
    try:
        key = PersonalityId(pid) if pid else PersonalityId.bilanciato
    except ValueError:
        key = PersonalityId.bilanciato
    prompt, confirm = _PROMPTS[key]
    return Personality(
        id=key,
        label=t(f"personalities.{key.value}.label", lang),
        description=t(f"personalities.{key.value}.description", lang),
        prompt=prompt,
        requires_confirmation=confirm,
    )


def all_personalities(lang: str = "en") -> list[Personality]:
    return [get_personality(p, lang) for p in _PROMPTS]


PERSONALITIES = {p: get_personality(p) for p in _PROMPTS}  # English labels; kept for imports
