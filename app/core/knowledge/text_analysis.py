"""Tokenisation shared by BM25 indexing, search highlighting and heuristics."""

from __future__ import annotations

import re
from functools import lru_cache

import Stemmer

WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9_\-\.']*[A-Za-zÀ-ÖØ-öø-ÿ0-9]|[A-Za-zÀ-ÖØ-öø-ÿ0-9]")

STOPWORDS_IT = set(
    """a ad al allo alla ai agli alle anche avere aveva c che chi ci coi col come con contro cui da dal dallo
    dalla dai dagli dalle degli dei del dell della delle dello di do dove e ed è era essere fa fra gli ha hanno
    ho i il in io l la le lei li lo loro lui ma mi mia mio ne negli nei nel nell nella nelle nello noi non nostro
    o od per perché più poi può quale quando quanto quella quelle quelli quello questa queste questi questo
    se sei si sia siamo sono su sua sue sugli sui sul sull sulla sulle sullo suo tra tu tua tuo tutti tutto
    un una uno vi voi un' l' all' dell' nell' sull' dall' quest' po""".split()
)
STOPWORDS_EN = set(
    """a an and are as at be by for from has have in is it its of on or that the this to was were will with
    which who what when where how not no but if then than so such into over under per via""".split()
)
STOPWORDS = STOPWORDS_IT | STOPWORDS_EN


@lru_cache(maxsize=4)
def _stemmer(lang: str) -> Stemmer.Stemmer:
    return Stemmer.Stemmer(lang)


def normalize(word: str) -> str:
    w = word.lower().strip(".'-")
    if "'" in w:  # l'ordine -> ordine
        w = w.split("'")[-1]
    return w


def stem(word: str, lang: str = "italian") -> str:
    w = normalize(word)
    if any(c.isdigit() for c in w):
        return w  # product codes (PX200, M8) stay exact
    return _stemmer(lang).stemWord(w)


def tokens(text: str, lang: str = "italian") -> list[str]:
    out = []
    for m in WORD.finditer(text):
        w = normalize(m.group(0))
        if not w or w in STOPWORDS or len(w) < 2 and not w.isdigit():
            continue
        out.append(stem(w, lang))
    return out


def token_spans(text: str, lang: str = "italian") -> list[tuple[int, int, str]]:
    """(start, end, stem) for every content word — used to highlight matches."""
    out = []
    for m in WORD.finditer(text):
        w = normalize(m.group(0))
        if not w or w in STOPWORDS:
            continue
        out.append((m.start(), m.end(), stem(w, lang)))
    return out
