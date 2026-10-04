"""Spreadsheet cards: the summary of a CSV / Excel / SQLite document IS its computed facts.

No model call. Each fact gets an id (S1, S2…) that notes cite as evidence, so hours estimates
rest on real counts (rows, cadence, empty cells…) instead of numbers a model wrote.
"""

from __future__ import annotations

import warnings
from typing import Any

import pandas as pd

from app.core.agents.explorer.Rag.signal import heuristic_signal
from app.core.knowledge.ingest import title_for
from app.core.knowledge.service import KnowledgeService
from app.core.knowledge.tabular.loader import list_sheets, load_frame
from app.core.knowledge.tabular.tools import _to_dates
from app.data.models.document import Document, DocumentKind
from app.data.models.knowledge import CARD_VERSION, CardKind, CardStat, DocumentCard
from app.i18n import t

MAX_STATS = 15
MAX_DATE_COLS = 2
MAX_TOP_COLS = 2
SAMPLE_ROWS = 3


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cadence(days: pd.Series) -> tuple[str | None, float]:
    """(cadence key, median gap) from sorted distinct days."""
    if len(days) < 3:
        return None, 0.0
    median = float(days.diff().dropna().dt.days.median())
    key = ("daily" if median <= 1.5 else "weekly" if 5 <= median <= 9 else
           "monthly" if 25 <= median <= 35 else None)
    return key, median


def _sheet_stats(df: pd.DataFrame, sheet: str | None, header: int, lang: str) -> tuple[list[dict[str, Any]], str | None]:
    """Facts for one sheet (without ids) and the best cadence phrase for the card sentence."""
    out: list[dict[str, Any]] = []
    n_rows, n_cols = len(df), len(df.columns)
    label = sheet or "csv"
    cols = [str(c) for c in df.columns]
    out.append({"metric": "rows", "columns": cols[:12], "value": {"rows": n_rows, "columns": n_cols},
                "text": t("cards.stat_rows", lang, sheet=label, rows=n_rows, cols=n_cols, names=", ".join(cols[:8]))})
    best_cadence = None
    dated = 0
    for c in df.columns:
        if dated >= MAX_DATE_COLS or pd.api.types.is_numeric_dtype(df[c]):
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s = _to_dates(df[c])
        if s.notna().sum() < max(3, n_rows * 0.6):
            continue
        dated += 1
        days = s.dropna().dt.normalize().drop_duplicates().sort_values()
        start, end = s.min().date().isoformat(), s.max().date().isoformat()
        key, median = _cadence(days)
        per = round(n_rows / max(len(days), 1), 1)
        if key:
            phrase = t(f"cards.cadence_{key}", lang)
            best_cadence = best_cadence or phrase
            text = t("cards.stat_cadence", lang, column=str(c), cadence=phrase, per=per, start=start, end=end)
        else:
            text = t("cards.stat_dates", lang, column=str(c), start=start, end=end, days=len(days))
        out.append({"metric": "cadence" if key else "date_range", "columns": [str(c)],
                    "value": {"start": start, "end": end, "distinct_days": int(len(days)), "median_gap_days": median,
                              "rows_per_day": per, "cadence": key}, "text": text})
    empty = [(str(c), float(df[c].isna().mean())) for c in df.columns if n_rows and df[c].isna().mean() > 0.05]
    if empty:
        empty.sort(key=lambda x: -x[1])
        out.append({"metric": "empty_rate", "columns": [c for c, _ in empty[:4]],
                    "value": {c: round(r, 3) for c, r in empty[:4]},
                    "text": t("cards.stat_empty", lang, items=", ".join(f"«{c}» {round(r * 100)}%" for c, r in empty[:4]))})
    tops = 0
    for c in df.columns:
        if tops >= MAX_TOP_COLS or n_rows < 10:
            break
        uniq = df[c].nunique(dropna=True)
        if 2 <= uniq <= 8 and not pd.api.types.is_float_dtype(df[c]):
            vc = df[c].value_counts(normalize=True).head(4)
            tops += 1
            out.append({"metric": "top_values", "columns": [str(c)],
                        "value": {str(k): round(float(v), 3) for k, v in vc.items()},
                        "text": t("cards.stat_top", lang, column=str(c),
                                  items=", ".join(f"{k} {round(v * 100)}%" for k, v in vc.items()))})
    dupes = int(df.duplicated().sum())
    if dupes:
        out.append({"metric": "duplicates", "columns": [], "value": dupes, "text": t("cards.stat_dupes", lang, count=dupes)})
    last = _col_letter(max(n_cols - 1, 0))
    for i in range(min(SAMPLE_ROWS, n_rows)):
        row = df.iloc[i]
        excel_row = header + 2 + i  # 1-based, after the header row
        vals = ", ".join(f"{c}={row[c]}" for c in df.columns[:8] if pd.notna(row[c]))[:220]
        out.append({"metric": "sample_row", "columns": cols[:8], "value": None,
                    "text": t("cards.stat_sample", lang, row=excel_row, values=vals),
                    "cells": f"{label}!A{excel_row}:{last}{excel_row}"})
    for s in out:
        s["sheet"] = sheet
    return out, best_cadence


def table_card(ks: KnowledgeService, doc: Document, lang: str = "en") -> DocumentCard:
    """Spreadsheet card computed locally: title, a templated sentence and up to 15 cited facts."""
    path = ks.file_path(doc)
    raw: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    total_rows, cadence = 0, None
    sheets = list_sheets(path, doc.kind)
    for sheet in sheets:
        try:
            df, header = load_frame(path, doc.kind, None if doc.kind == DocumentKind.csv else sheet)
        except Exception:
            continue
        stats, phrase = _sheet_stats(df, None if doc.kind == DocumentKind.csv else sheet, header, lang)
        total_rows += len(df)
        cadence = cadence or phrase
        raw += [s for s in stats if s["metric"] != "sample_row"]
        samples += [s for s in stats if s["metric"] == "sample_row"]
    chosen = raw[: MAX_STATS - min(len(samples), SAMPLE_ROWS)] + samples[:SAMPLE_ROWS]
    stats = [CardStat(id=f"S{i + 1}", **s) for i, s in enumerate(chosen)]
    profiles = ks.docs.profiles(doc.id)
    periodicity = [{"cadence": (s.value or {}).get("cadence")} for s in stats if s.metric == "cadence"]
    signal, reasons = heuristic_signal(doc, ks.docs.markdown(doc.id), profiles, periodicity)
    key = "cards.table_sentence_cadence" if cadence else "cards.table_sentence"
    facts = [s for s in stats if s.metric != "sample_row"]
    cad = next((s for s in stats if s.metric == "cadence"), None)
    empty = next((s for s in stats if s.metric == "empty_rate"), None)
    dupes = next((s for s in stats if s.metric == "duplicates"), None)
    card = DocumentCard(
        org_id=ks.org_id, document_id=doc.id, sha256=doc.sha256, version=CARD_VERSION, kind=CardKind.table,
        title=title_for(doc.filename), department=doc.department,
        sentence=t(key, lang, rows=total_rows, count=len(sheets), cadence=cadence or ""),
        summary="; ".join(s.text for s in facts)[:1200],
        signals={"rep": round(signal, 2), "rows": total_rows, "sheets": len(sheets),
                 "freq": (cad.value or {}).get("cadence") if cad else None,
                 "per_day": (cad.value or {}).get("rows_per_day") if cad else None,
                 "empty": ",".join(f"{c}:{round(r * 100)}%" for c, r in (empty.value or {}).items()) if empty else None,
                 "dupes": dupes.value if dupes else None},
        signal=round(signal, 3), reasons=reasons, stats=stats, model=None,
    )
    card.quotes = [e for s in facts[:3] for e in card.evidence_for(s.id)]
    return card
