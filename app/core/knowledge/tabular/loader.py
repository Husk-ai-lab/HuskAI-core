"""Load CSV / Excel / SQLite files as pandas DataFrames with header detection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from app.data.models.document import ColumnProfile, DocumentKind, TabularProfile

MAX_SCAN_ROWS = 200_000


def list_sheets(path: Path, kind: DocumentKind) -> list[str]:
    if kind == DocumentKind.csv:
        return ["csv"]
    if kind == DocumentKind.db:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
        finally:
            conn.close()
    return list(pd.ExcelFile(path).sheet_names)


def _detect_header(raw: pd.DataFrame) -> int:
    """First row (of the first 15) that looks like a header: mostly non-empty strings."""
    best, best_score = 0, -1.0
    for i in range(min(15, len(raw))):
        row = raw.iloc[i]
        filled = row.notna().sum()
        if filled == 0:
            continue
        strings = sum(isinstance(v, str) and not _is_number(v) for v in row if pd.notna(v))
        score = strings / max(len(row), 1) + filled / max(len(row), 1) * 0.5
        if score > best_score + 0.15:
            best, best_score = i, score
    return best


def _is_number(v: str) -> bool:
    try:
        float(str(v).replace(",", "."))
        return True
    except ValueError:
        return False


def load_frame(path: Path, kind: DocumentKind, sheet: str | None = None) -> tuple[pd.DataFrame, int]:
    """Return (dataframe, header_row_index)."""
    if kind == DocumentKind.csv:
        sep = "\t" if path.suffix.lower() == ".tsv" else None
        df = pd.read_csv(path, sep=sep, engine="python", nrows=MAX_SCAN_ROWS)
        return _clean(df), 0
    if kind == DocumentKind.db:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            table = sheet or list_sheets(path, kind)[0]
            df = pd.read_sql_query(f'SELECT * FROM "{table}" LIMIT {MAX_SCAN_ROWS}', conn)
            return _clean(df), 0
        finally:
            conn.close()
    sheet_name = sheet if sheet is not None else 0
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=MAX_SCAN_ROWS)
    header = _detect_header(raw)
    df = raw.iloc[header + 1 :].copy()
    cols = []
    for i, c in enumerate(raw.iloc[header].tolist()):
        name = str(c).strip() if pd.notna(c) else f"colonna_{i + 1}"
        while name in cols:
            name += "_"
        cols.append(name)
    df.columns = cols
    return _clean(df), header


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df = df.reset_index(drop=True)
    for c in df.columns:
        if df[c].dtype == object:
            converted = pd.to_numeric(df[c], errors="coerce")
            if converted.notna().sum() >= max(1, int(df[c].notna().sum() * 0.9)):
                df[c] = converted
    return df


def profile_frame(df: pd.DataFrame) -> list[ColumnProfile]:
    out = []
    for c in df.columns:
        s = df[c]
        sample = [str(v)[:40] for v in s.dropna().unique()[:5]]
        out.append(
            ColumnProfile(
                name=str(c), dtype=str(s.dtype), non_null=int(s.notna().sum()), unique=int(s.nunique()), sample=sample
            )
        )
    return out


def profile_file(path: Path, kind: DocumentKind, org_id: str, document_id: str) -> list[TabularProfile]:
    profiles = []
    for sheet in list_sheets(path, kind):
        try:
            df, header = load_frame(path, kind, None if kind == DocumentKind.csv else sheet)
        except Exception:
            continue
        profiles.append(
            TabularProfile(
                org_id=org_id,
                document_id=document_id,
                sheet=sheet,
                n_rows=len(df),
                n_cols=len(df.columns),
                header_row=header,
                columns=profile_frame(df),
            )
        )
    return profiles
