"""Document conversion: everything becomes markdown text (MarkItDown) + tabular profiles.

Files never leave the machine. Spreadsheets are additionally profiled sheet by sheet so
agents can navigate them as tables instead of flattened text (Excel navigator).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.core.knowledge.tabular.loader import profile_file
from app.data.models.document import DocumentKind, TabularProfile
from app.helpers.text import truncate
from app.i18n import t

EXT_KIND = {
    ".pdf": DocumentKind.pdf,
    ".docx": DocumentKind.docx,
    ".doc": DocumentKind.docx,
    ".xlsx": DocumentKind.xlsx,
    ".xlsm": DocumentKind.xlsx,
    ".xls": DocumentKind.xlsx,
    ".csv": DocumentKind.csv,
    ".tsv": DocumentKind.csv,
    ".pptx": DocumentKind.pptx,
    ".md": DocumentKind.md,
    ".markdown": DocumentKind.md,
    ".txt": DocumentKind.txt,
    ".json": DocumentKind.txt,
    ".db": DocumentKind.db,
    ".sqlite": DocumentKind.db,
    ".sqlite3": DocumentKind.db,
}

MAX_UPLOAD_BYTES = 60 * 1024 * 1024


def kind_for(filename: str) -> DocumentKind:
    return EXT_KIND.get(Path(filename).suffix.lower(), DocumentKind.other)


def title_for(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(stem.split()) or filename


@dataclass
class Converted:
    markdown: str
    excerpt: str
    rows: int | None = None
    profiles: list[TabularProfile] = field(default_factory=list)


def _sqlite_markdown(path: Path, limit_rows: int = 50) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        parts = [f"# Database {path.name}"]
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            parts.append(f"\n## Tabella {name}\n")
            parts.append("| " + " | ".join(cols) + " |")
            parts.append("|" + "---|" * len(cols))
            for row in conn.execute(f'SELECT * FROM "{name}" LIMIT {limit_rows}'):
                parts.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
        return "\n".join(parts)
    finally:
        conn.close()


def _excerpt(markdown: str) -> str:
    prose = [
        line.strip()
        for line in markdown.splitlines()
        if line.strip() and not line.lstrip().startswith(("|", "#", "---", "```"))
    ]
    text = " ".join(prose) or " ".join(markdown.split())
    return truncate(text, 600)


def convert(path: Path, kind: DocumentKind, org_id: str, document_id: str, lang: str = "en") -> Converted:
    if kind == DocumentKind.db:
        md = _sqlite_markdown(path)
    elif kind in (DocumentKind.md, DocumentKind.txt):
        md = path.read_text("utf-8", errors="replace")
    else:
        from markitdown import MarkItDown

        md = MarkItDown(enable_plugins=False).convert(str(path)).text_content or ""
    profiles: list[TabularProfile] = []
    rows = None
    if kind.is_tabular:
        profiles = profile_file(path, kind, org_id, document_id)
        rows = sum(p.n_rows for p in profiles) or None
    excerpt = _excerpt(md)
    if profiles:  # tables read better as a structure summary than as raw markdown rows
        parts = []
        for p in profiles[:3]:
            missing = [c.name for c in p.columns if c.non_null < p.n_rows]
            line = t("knowledge.excerpt_sheet", lang, sheet=p.sheet, count=p.n_rows,
                     columns=", ".join(c.name for c in p.columns[:12]))
            if missing:
                line += " " + t("knowledge.excerpt_missing", lang, columns=", ".join(missing[:6]))
            parts.append(line)
        excerpt = " ".join(parts)
    return Converted(markdown=md, excerpt=excerpt, rows=rows, profiles=profiles)
