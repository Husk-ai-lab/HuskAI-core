"""Data-scientist tools over CSV / Excel / SQLite documents.

Used by both internal agents (explorer and builder) as LangChain tools. Numbers stay
exact (read from the file, never from flattened text) and every result is size-capped so
it fits in a model's context.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.knowledge.tabular.loader import list_sheets, load_frame, profile_frame
from app.data.models.document import Document, DocumentKind

MAX_ROWS_OUT = 40
MAX_CHARS_OUT = 6000

OPS = {
    "==": lambda s, v: s == v,
    "!=": lambda s, v: s != v,
    ">": lambda s, v: s > v,
    ">=": lambda s, v: s >= v,
    "<": lambda s, v: s < v,
    "<=": lambda s, v: s <= v,
    "contains": lambda s, v: s.astype(str).str.contains(str(v), case=False, na=False),
    "isnull": lambda s, v: s.isna(),
    "notnull": lambda s, v: s.notna(),
}


class TabularError(ValueError):
    pass


def _records(df: pd.DataFrame, limit: int = MAX_ROWS_OUT) -> list[dict[str, Any]]:
    out = df.head(limit).astype(object).where(df.head(limit).notna(), None)
    return json.loads(out.to_json(orient="records", date_format="iso", force_ascii=False))


def _cap(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_CHARS_OUT and "rows" in payload:
        while len(json.dumps(payload, ensure_ascii=False, default=str)) > MAX_CHARS_OUT and payload["rows"]:
            payload["rows"] = payload["rows"][: max(1, len(payload["rows"]) // 2)]
            payload["truncated"] = True
            if len(payload["rows"]) == 1:
                break
    return payload


class TabularTools:
    def __init__(self, resolve: Callable[[str], tuple[Document, Path] | None]):
        """`resolve(document_id)` returns the document and its file path (org-scoped)."""
        self._resolve = resolve
        self._frames: dict[tuple[str, str | None], pd.DataFrame] = {}

    def _frame(self, document_id: str, sheet: str | None) -> tuple[pd.DataFrame, Document]:
        res = self._resolve(document_id)
        if not res:
            raise TabularError(f"document {document_id} not found")
        doc, path = res
        if not doc.kind.is_tabular:
            raise TabularError(f"{doc.filename} is not a data file (CSV/Excel/DB)")
        key = (document_id, sheet)
        if key not in self._frames:
            df, _ = load_frame(path, doc.kind, None if doc.kind == DocumentKind.csv else sheet)
            self._frames[key] = df
        return self._frames[key], doc

    def list_sheets(self, document_id: str) -> dict[str, Any]:
        res = self._resolve(document_id)
        if not res:
            raise TabularError(f"document {document_id} not found")
        doc, path = res
        return {"document": doc.filename, "sheets": list_sheets(path, doc.kind)}

    def describe_table(self, document_id: str, sheet: str | None = None) -> dict[str, Any]:
        df, doc = self._frame(document_id, sheet)
        return _cap(
            {
                "document": doc.filename,
                "sheet": sheet,
                "rows": _records(df, 5),
                "n_rows": len(df),
                "columns": [c.model_dump() for c in profile_frame(df)],
            }
        )

    def filter_rows(
        self, document_id: str, column: str, op: str, value: Any = None, sheet: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        df, doc = self._frame(document_id, sheet)
        if column not in df.columns:
            raise TabularError(f"column '{column}' not found; columns: {list(df.columns)[:30]}")
        if op not in OPS:
            raise TabularError(f"invalid operator: {op}")
        s = df[column]
        if op not in ("contains", "isnull", "notnull") and pd.api.types.is_numeric_dtype(s):
            value = float(value)
        mask = OPS[op](s, value)
        sub = df[mask]
        return _cap({"document": doc.filename, "matches": int(mask.sum()), "rows": _records(sub, min(limit, MAX_ROWS_OUT))})

    def aggregate(
        self, document_id: str, column: str, func: str = "sum", group_by: str | None = None, sheet: str | None = None
    ) -> dict[str, Any]:
        df, doc = self._frame(document_id, sheet)
        if func not in ("sum", "mean", "min", "max", "count", "median", "nunique"):
            raise TabularError(f"invalid function: {func}")
        for c in filter(None, (column, group_by)):
            if c not in df.columns:
                raise TabularError(f"column '{c}' not found; columns: {list(df.columns)[:30]}")
        if group_by:
            res = df.groupby(group_by, dropna=False)[column].agg(func).sort_values(ascending=False)
            rows = [{"group": str(k), func: (None if pd.isna(v) else float(v))} for k, v in res.head(MAX_ROWS_OUT).items()]
            return _cap({"document": doc.filename, "rows": rows, "groups": int(len(res))})
        v = df[column].agg(func)
        return {"document": doc.filename, "column": column, func: None if pd.isna(v) else float(v)}

    def value_counts(self, document_id: str, column: str, sheet: str | None = None, top: int = 15) -> dict[str, Any]:
        df, doc = self._frame(document_id, sheet)
        if column not in df.columns:
            raise TabularError(f"column '{column}' not found; columns: {list(df.columns)[:30]}")
        vc = df[column].value_counts(dropna=False).head(top)
        return {"document": doc.filename, "column": column, "counts": {str(k): int(v) for k, v in vc.items()}}

    def detect_periodicity(self, document_id: str, date_column: str | None = None, sheet: str | None = None) -> dict[str, Any]:
        """Does this table grow on a regular cadence (daily shift reports, weekly orders)?"""
        df, doc = self._frame(document_id, sheet)
        candidates = [date_column] if date_column else list(df.columns)
        for c in candidates:
            if c not in df.columns:
                continue
            if not date_column and pd.api.types.is_numeric_dtype(df[c]):
                continue  # integers would parse as 1970 timestamps
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                s = _to_dates(df[c])
            if s.notna().sum() < max(3, len(df) * 0.6):
                continue
            days = s.dropna().dt.normalize().drop_duplicates().sort_values()
            if len(days) < 3:
                continue
            gaps = days.diff().dropna().dt.days
            median = float(gaps.median())
            cadence = (
                "daily" if median <= 1.5 else "weekly" if 5 <= median <= 9 else
                "monthly" if 25 <= median <= 35 else f"every ~{median:g} days"
            )
            return {
                "document": doc.filename,
                "date_column": c,
                "distinct_days": int(len(days)),
                "median_gap_days": median,
                "regularity": round(float((gaps == gaps.median()).mean()), 2),
                "cadence": cadence,
                "rows_per_period": round(len(df) / max(len(days), 1), 1),
            }
        return {"document": doc.filename, "cadence": None, "note": "no date column recognised"}

    def compare_versions(
        self, document_a: str, document_b: str, key_column: str, sheet_a: str | None = None, sheet_b: str | None = None
    ) -> dict[str, Any]:
        """Row-level diff between two tables (e.g. BOM revision B vs C)."""
        a, da = self._frame(document_a, sheet_a)
        b, db = self._frame(document_b, sheet_b)
        for df, d in ((a, da), (b, db)):
            if key_column not in df.columns:
                raise TabularError(f"key column '{key_column}' not found in {d.filename}")
        a_i, b_i = a.set_index(key_column), b.set_index(key_column)
        added = sorted(map(str, set(b_i.index) - set(a_i.index)))
        removed = sorted(map(str, set(a_i.index) - set(b_i.index)))
        common_cols = [c for c in a_i.columns if c in b_i.columns]
        changed = []
        for k in set(a_i.index) & set(b_i.index):
            ra, rb = a_i.loc[k], b_i.loc[k]
            if isinstance(ra, pd.DataFrame) or isinstance(rb, pd.DataFrame):
                continue
            diffs = {
                c: {"before": _py(ra[c]), "after": _py(rb[c])}
                for c in common_cols
                if not (pd.isna(ra[c]) and pd.isna(rb[c])) and str(ra[c]) != str(rb[c])
            }
            if diffs:
                changed.append({"key": str(k), "changes": diffs})
        return _cap(
            {
                "a": da.filename,
                "b": db.filename,
                "added": added[:MAX_ROWS_OUT],
                "removed": removed[:MAX_ROWS_OUT],
                "changed_count": len(changed),
                "rows": changed[:MAX_ROWS_OUT],
            }
        )

    def missing_values(self, document_id: str, sheet: str | None = None) -> dict[str, Any]:
        df, doc = self._frame(document_id, sheet)
        nulls = {str(c): int(n) for c, n in df.isna().sum().items() if n}
        return {"document": doc.filename, "n_rows": len(df), "missing_by_column": nulls}

    def read_cells(self, document_id: str, start_row: int = 0, n_rows: int = 20, columns: list[str] | None = None,
                   sheet: str | None = None) -> dict[str, Any]:
        df, doc = self._frame(document_id, sheet)
        sub = df.iloc[start_row : start_row + min(n_rows, MAX_ROWS_OUT)]
        if columns:
            sub = sub[[c for c in columns if c in sub.columns]]
        return _cap({"document": doc.filename, "start_row": start_row, "rows": _records(sub)})


def _to_dates(col: pd.Series) -> pd.Series:
    """ISO dates (2026-09-01) are year-first; Italian sheets usually write 01/09/2026."""
    if pd.api.types.is_datetime64_any_dtype(col):
        return col
    as_text = col.dropna().astype(str)
    iso = as_text.str.match(r"^\d{4}-\d{2}-\d{2}").mean() if len(as_text) else 0
    return pd.to_datetime(col, errors="coerce", dayfirst=iso < 0.5, format="ISO8601" if iso >= 0.5 else None)


def _py(v: Any) -> Any:
    if pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v
