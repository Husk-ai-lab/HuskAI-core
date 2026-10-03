"""Automation-signal heuristics: cheap, explainable, run before any LLM call.

A document full of recurring, manual, cross-department work has high signal. The
heuristic score is blended with the LLM's judgement in the explorer.
"""

from __future__ import annotations

import re

from app.data.models.document import Document, TabularProfile

FREQUENCY = [
    r"ogni (giorno|sera|mattina|settimana|mese|turno|lunedì|venerdì|ordine|revisione|lotto|consegna)",
    r"giornalier[oaie]", r"settimanal[ei]", r"mensil[ei]", r"quotidian[oaie]", r"a fine turno",
    r"\bdaily\b", r"\bweekly\b", r"every (day|week|month|shift)", r"periodicamente", r"di routine",
]
MANUAL = [
    r"a mano", r"manualmente", r"ricopi", r"trascri", r"copia(re|to)? e incolla", r"incoll",
    r"inserit[oaie] nel gestionale", r"reinserit", r"compila(re|to)?", r"foglio excel", r"stampa(re|to)?",
    r"controll(o|are) visiv", r"verifica(re)? a vista", r"manually", r"re-?type", r"copy",
]
HANDOFF = [
    r"invia(re|to)? (a|al|alla|all')", r"inoltra", r"comunica(re)? (a|al|alla)", r"passa(re|to)? (a|al|alla)",
    r"ufficio (tecnico|acquisti|qualità|commerciale)", r"magazzino", r"produzione", r"reparto",
    r"responsabile", r"approva(zione|re)", r"firma", r"e-?mail", r"gestionale|\berp\b|\bmes\b",
]
RULES = [r"\bse\b.*\ballora\b", r"\bse (manca|mancano|supera|è inferiore|è superiore)", r"soglia", r"entro \d+"]

_C = {k: [re.compile(p, re.I) for p in v] for k, v in
      {"frequency": FREQUENCY, "manual": MANUAL, "handoff": HANDOFF, "rules": RULES}.items()}

LABELS = {  # internal (fed to the model), not shown to users
    "frequency": "recurring activities",
    "manual": "manual work / re-typing",
    "handoff": "handoffs between departments or systems",
    "rules": "explicit decision rules",
}


def heuristic_signal(doc: Document, markdown: str, profiles: list[TabularProfile],
                     periodicity: list[dict] | None = None) -> tuple[float, list[str]]:
    text = markdown[:60_000]
    words = max(len(text.split()), 1)
    reasons = []
    score = 0.0
    weights = {"frequency": 0.32, "manual": 0.32, "handoff": 0.18, "rules": 0.18}
    for key, patterns in _C.items():
        hits = sum(len(p.findall(text)) for p in patterns)
        density = hits / (words / 400)  # hits per ~page
        part = min(density / 3, 1.0)
        if hits:
            reasons.append(f"{LABELS[key]} ({hits})")
        score += weights[key] * part
    if doc.kind.is_tabular and profiles:
        score = max(score, 0.25)
        missing = sum(c.non_null < p.n_rows for p in profiles for c in p.columns)
        if missing:
            score += 0.1
            reasons.append(f"columns with missing data to fill in ({missing})")
        for p in periodicity or []:
            if p.get("cadence"):
                score += 0.25 if p.get("regularity", 0) >= 0.5 else 0.12
                reasons.append(f"data with a {p['cadence']} cadence")
                break
    return round(min(score, 1.0), 3), reasons
