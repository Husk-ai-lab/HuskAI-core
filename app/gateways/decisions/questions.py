"""Serenity-style decisions as typed questions (wording follows the Serenity definitions; it/en).

The state is built from document cards; questions reference documents by short alias (D01…).
"""

from __future__ import annotations

from app.gateways.decisions.base import Question

RELATIONS = {
    "handoff": ("Passaggio di dati tra reparti", "Data handoff between departments"),
    "revision": ("Revisione o sostituzione", "Revision or replacement"),
    "same_item": ("Stesso lavoro, ordine o articolo", "Same job, order or item"),
    "rule_record": ("Regola e registrazione della sua esecuzione", "Rule and its execution record"),
    "duplicate": ("Contenuto duplicato", "Duplicate content"),
}

# hours of repetitive work per month: (label it, label en, midpoint hours/month)
HOUR_BUCKETS = [
    ("meno di 1 ora", "under 1 hour", 0.5),
    ("1–2 ore", "1–2 hours", 1.5),
    ("2–5 ore", "2–5 hours", 3.5),
    ("5–10 ore", "5–10 hours", 7.5),
    ("10–20 ore", "10–20 hours", 15.0),
    ("20–50 ore", "20–50 hours", 35.0),
    ("50–100 ore", "50–100 hours", 75.0),
    ("oltre 100 ore", "over 100 hours", 130.0),
]
WEEKS_PER_MONTH = 4.33


def _l(lang: str, it: str, en: str) -> str:
    return it if lang == "it" else en


def doc_related(a: str, b: str, lang: str) -> Question:
    return Question(type="noul", instructions=_l(
        lang,
        f"I documenti {a} e {b} sono collegati: riguardano la stessa pratica, si passano dati o uno rimanda "
        "all'altro? Stessa azienda, reparto o vocabolario non bastano.",
        f"Are documents {a} and {b} related: do they concern the same matter, pass data to each other, or does one "
        "refer to the other? Same company, department or vocabulary is not enough."),
        criteria={"true": _l(lang, "collegati", "related"), "false": _l(lang, "non collegati", "not related")})


def relation_type(a: str, b: str, lang: str) -> Question:
    return Question(type="choice", instructions=_l(lang, f"Che cosa collega {a} e {b}?", f"What links {a} and {b}?"),
                    criteria={k: _l(lang, it, en) for k, (it, en) in RELATIONS.items()})


def needs_deeper_research(doc: str, lang: str) -> Question:
    return Question(type="noul", instructions=_l(
        lang,
        f"Il documento {doc} va approfondito, perché il riassunto non riporta informazioni che contiene "
        "(quantità, date, codici, scadenze, eccezioni)?",
        f"Does document {doc} need deeper research because the summary misses information it holds "
        "(quantities, dates, codes, deadlines, exceptions)?"),
        criteria={"true": _l(lang, "sì, va riletto", "yes, re-read it"), "false": "no"})


def repetitiveness(lang: str) -> Question:
    return Question(type="score", instructions=_l(
        lang,
        "Quanto è ripetitivo il lavoro registrato da questi documenti (modelli fissi, ID sequenziali, date "
        "periodiche, passaggi identici riga dopo riga = alto; testo unico e occasionale = basso)?",
        "How repetitive is the work these documents record (fixed template, sequential IDs, periodic dates, "
        "identical steps row after row = high; a bespoke one-off text = low)?"),
        criteria=[f"{i / 10:.1f}–{(i + 1) / 10:.1f}" for i in range(10)])


def hours_wasted(lang: str) -> Question:
    return Question(type="score", instructions=_l(
        lang,
        "Se il lavoro registrato da questi documenti è ripetitivo, quante ore al mese richiede? Calcola da "
        "frequenza × durata indicate nei documenti.",
        "If the work these documents record is repetitive, how many hours a month does it take? Compute it from "
        "the frequency × duration the documents state."),
        criteria=[_l(lang, it, en) for it, en, _ in HOUR_BUCKETS])


def expected_hours_per_week(probabilities: dict[str, float]) -> tuple[float, float]:
    """(hours/week from the bucket midpoints weighted by probability, spread in buckets)."""
    probs = [float(probabilities.get(str(i), 0.0)) for i in range(len(HOUR_BUCKETS))]
    total = sum(probs) or 1.0
    probs = [p / total for p in probs]
    month = sum(p * mid for p, (_, _, mid) in zip(probs, HOUR_BUCKETS, strict=True))
    pos = sum(i * p for i, p in enumerate(probs))
    spread = sum(p * (i - pos) ** 2 for i, p in enumerate(probs)) ** 0.5
    return round(month / WEEKS_PER_MONTH, 1), round(spread, 2)


def score_value(probabilities: dict[str, float], levels: int = 10) -> float:
    """0–1 value of a 10-band score (expected band centre)."""
    probs = [float(probabilities.get(str(i), 0.0)) for i in range(levels)]
    total = sum(probs) or 1.0
    return round(sum((i + 0.5) / levels * p for i, p in enumerate(probs)) / total, 3)
