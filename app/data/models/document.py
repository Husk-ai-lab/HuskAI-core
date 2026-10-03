from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.data.models.common import HuskModel, OrgScoped
from app.helpers.time import utcnow


class DocumentKind(StrEnum):
    pdf = "pdf"
    docx = "docx"
    xlsx = "xlsx"
    csv = "csv"
    pptx = "pptx"
    md = "md"
    txt = "txt"
    db = "db"
    other = "other"

    @property
    def is_tabular(self) -> bool:
        return self in (DocumentKind.xlsx, DocumentKind.csv, DocumentKind.db)


class DocumentStatus(StrEnum):
    pending = "pending"
    ingested = "ingested"
    failed = "failed"


class Document(OrgScoped):
    id: str
    filename: str
    title: str
    kind: DocumentKind
    department: str | None = None
    size_bytes: int = 0
    rows: int | None = None  # tabular documents only
    sha256: str
    status: DocumentStatus = DocumentStatus.pending
    excerpt: str | None = None
    error: str | None = None
    authorized_at: datetime  # explicit user authorisation before any processing
    uploaded_by: str
    source_path: str | None = None  # relative path when uploaded as part of a folder
    created_at: datetime = Field(default_factory=utcnow)


class DocumentWithUsage(Document):
    used_by: list[UsedBy] = Field(default_factory=list)


class UsedBy(HuskModel):
    automation_id: str
    title: str


class Chunk(OrgScoped):
    id: str
    document_id: str
    ordinal: int
    heading: str | None = None
    text: str
    start: int = 0
    end: int = 0


class Highlight(HuskModel):
    start: int
    end: int


class SearchHit(HuskModel):
    document_id: str
    chunk_id: str
    score: float
    heading: str | None = None
    text: str
    highlights: list[Highlight] = Field(default_factory=list)


class DocumentSearchResult(HuskModel):
    document: Document
    matches: int
    best: SearchHit


class ColumnProfile(HuskModel):
    name: str
    dtype: str
    non_null: int
    unique: int
    sample: list[str] = Field(default_factory=list)


class TabularProfile(OrgScoped):
    document_id: str
    sheet: str
    n_rows: int
    n_cols: int
    header_row: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)


DocumentWithUsage.model_rebuild()
