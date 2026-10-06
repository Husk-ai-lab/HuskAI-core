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


# --- document language (English / Italian only, for now) ------------------------------------------------------
# Function words that mark one language and are NOT also common words of the other ("a", "in", "per", "no" are
# left out on purpose). Dependency-free and deterministic; unsure means English (the product default).
_IT_MARKERS = frozenset(
    """di il lo la gli le dei del della delle dello nel nella nei nelle sul sulla dal dalla che è sono era viene
    vengono non una uno con per ogni anche come più tra fra alla alle agli questo questa questi queste quello
    sia essere hanno deve devono se poi quando dove tutti tutto già ancora prima dopo mentre cui""".split()
) - {"per"}
_EN_MARKERS = frozenset(
    """the and of to is are was were with for that this these those from by each every which will be been has have
    had on at as an it its their they there when where then than not or if into over under after before while who
    what how can should must may""".split()
)
_ACCENTED = re.compile(r"[àèéìòù]")
_LETTERS = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
MIN_WORDS = 15  # fewer words than this (a spreadsheet of numbers, a title): not enough to say, so English


def detect_language(text: str) -> str:
    """"it" or "en". Counts marker words of each language in the first 20 000 characters; Italian needs a clear
    lead (accented vowels such as è, più, perché count a little for it). Anything unsure is "en"."""
    sample = (text or "")[:20_000].lower()
    words = _LETTERS.findall(sample)
    if len(words) < MIN_WORDS:
        return "en"
    it = sum(w in _IT_MARKERS for w in words) + len(_ACCENTED.findall(sample)) // 3
    en = sum(w in _EN_MARKERS for w in words)
    return "it" if it >= 3 and it > en * 1.3 else "en"
