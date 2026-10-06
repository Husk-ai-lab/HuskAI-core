"""The harness memory: company profile facts, network notes and summaries.

All of these are markdown documents for the agents (rendered by `render_markdown`),
persisted as versioned rows in SQLite.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.data.models.common import HuskModel, OrgScoped
from app.helpers.time import utcnow


class EvidenceRef(HuskModel):
    document_id: str
    chunk_id: str | None = None
    sheet: str | None = None
    quote: str | None = Field(None, max_length=400)
    stat_id: str | None = None  # a computed spreadsheet fact on the document card (S1, S2…)
    cells: str | None = None  # e.g. "Fatture!A2:K2"

    def label(self) -> str:
        parts = [self.document_id]
        if self.sheet:
            parts.append(f"sheet:{self.sheet}")
        if self.chunk_id:
            parts.append(self.chunk_id)
        return " · ".join(parts)


class FactCategory(StrEnum):
    identity = "identita"  # who the company is, sector, size
    process = "processo"
    systems = "sistemi"  # ERP, MES, tools
    people = "organizzazione"  # departments, roles (never named individuals)
    volumes = "volumi"
    constraints = "vincoli"


class Fact(OrgScoped):
    id: str
    text: str = Field(..., max_length=300)
    category: FactCategory = FactCategory.process
    importance: float = Field(0.5, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    references: int = 0  # how often agents consulted / reinforced it
    created_at: datetime = Field(default_factory=utcnow)

    def strength(self) -> float:
        """Eviction score: low strength is replaced first by a stronger side fact."""
        return self.importance + min(self.references, 10) * 0.02 + min(len(self.evidence), 3) * 0.03


class FactCandidate(HuskModel):
    text: str = Field(..., max_length=300)
    category: FactCategory = FactCategory.process
    importance: float = Field(0.5, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class FactDecision(StrEnum):
    added = "added"
    replaced = "replaced"
    rejected = "rejected"
    duplicate = "duplicate"


class FactOutcome(HuskModel):
    decision: FactDecision
    fact: Fact | None = None
    replaced: Fact | None = None
    reason: str = ""


class CompanyProfile(HuskModel):
    org_id: str
    facts: list[Fact] = Field(default_factory=list)
    max_facts: int = 30

    def render_markdown(self, lang: str = "en") -> str:
        from app.i18n import t

        lines = [f"# {t('memory.company_profile', lang)}", "",
                 f"_{t('memory.facts_count', lang, n=len(self.facts), max=self.max_facts)}_", ""]
        by_cat: dict[str, list[Fact]] = {}
        for f in sorted(self.facts, key=lambda f: -f.importance):
            by_cat.setdefault(f.category.value, []).append(f)
        for cat, facts in by_cat.items():
            lines.append(f"## {t(f'enums.fact_category.{cat}', lang)}")
            for f in facts:
                src = ", ".join(e.document_id for e in f.evidence[:2])
                lines.append(f"- [{f.id}] {f.text}" + (f" _({t('memory.source', lang)}: {src})_" if src else ""))
            lines.append("")
        return "\n".join(lines).strip() + "\n"


class NoteKind(StrEnum):
    task = "attivita"  # a repetitive activity
    handoff = "passaggio"  # data handed between departments
    check = "controllo"  # manual check
    entity = "entita"  # document, system, dataset the tasks revolve around


class NetworkNote(OrgScoped):
    """One atomic, always-complete note in an automation knowledge network."""

    id: str
    network_id: str
    kind: NoteKind = NoteKind.task
    title: str = Field(..., min_length=3, max_length=120)
    claim: str = Field(..., min_length=20, max_length=1200)
    frequency: str | None = None  # "ogni sera", "a ogni revisione", "settimanale"
    actors: list[str] = Field(default_factory=list)  # departments / roles, never person names
    evidence: list[EvidenceRef] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)  # ids of related notes ([[note_id]])
    hours_per_week: float | None = Field(None, ge=0, le=200)
    version: int = 1
    updated_at: datetime = Field(default_factory=utcnow)

    def render_markdown(self, lang: str = "en") -> str:
        from app.i18n import t

        out = [f"## {self.title}", f"`{self.id}` · {t(f'enums.note_kind.{self.kind.value}', lang)}"]
        out.append("")
        out.append(self.claim)
        meta = []
        if self.frequency:
            meta.append(f"**{t('memory.frequency', lang)}:** {self.frequency}")
        if self.actors:
            meta.append(f"**{t('memory.who', lang)}:** {', '.join(self.actors)}")
        if self.hours_per_week is not None:
            meta.append(f"**{t('memory.hours_week', lang)}:** {self.hours_per_week:g}")
        if meta:
            out += ["", " · ".join(meta)]
        if self.evidence:
            out += ["", f"**{t('memory.evidence', lang)}:**"] + [
                f"- {e.label()}" + (f" — «{e.quote}»" if e.quote else "") for e in self.evidence
            ]
        if self.links:
            out += ["", f"**{t('memory.links', lang)}:** " + " ".join(f"[[{lnk}]]" for lnk in self.links)]
        return "\n".join(out) + "\n"


class NetworkStatus(StrEnum):
    candidate = "candidate"
    kept = "kept"
    discarded = "discarded"


class Network(OrgScoped):
    id: str
    run_id: str
    label: str
    status: NetworkStatus = NetworkStatus.candidate
    automation_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class NetworkSummary(OrgScoped):
    automation_id: str
    network_id: str
    markdown: str
    version: int = 1
    updated_at: datetime = Field(default_factory=utcnow)


class DocumentSignal(OrgScoped):
    run_id: str
    document_id: str
    score: float = Field(..., ge=0, le=1)
    heuristic_score: float = Field(0, ge=0, le=1)
    llm_score: float | None = Field(None, ge=0, le=1)
    doc_type: str | None = None
    department: str | None = None
    reasons: list[str] = Field(default_factory=list)


class MemoryKind(StrEnum):
    company_profile = "company_profile"
    network_summary = "network_summary"
    network_notes = "network_notes"


class MemoryDoc(OrgScoped):
    id: str
    kind: MemoryKind
    scope: str  # "org" | automation id | network id
    version: int
    markdown: str
    updated_at: datetime = Field(default_factory=utcnow)


# --- document cards (discovery step 3: the shared summary memory) ------------------------------
CARD_VERSION = 3  # bump when the card prompt or the computed facts change: cached cards are rebuilt


class CardKind(StrEnum):
    text = "text"
    table = "table"


class CardEvidence(HuskModel):
    """Text: passage id + verbatim quote. Spreadsheet: a computed fact id (+ sheet, cells)."""

    document_id: str
    chunk_id: str | None = None
    quote: str | None = Field(None, max_length=400)
    stat_id: str | None = None
    sheet: str | None = None
    cells: str | None = None

    def to_ref(self) -> EvidenceRef:
        return EvidenceRef(document_id=self.document_id, chunk_id=self.chunk_id, quote=self.quote,
                           sheet=self.sheet, stat_id=self.stat_id, cells=self.cells)


class CardActivity(HuskModel):
    id: str  # A1, A2…
    what: str = Field(..., max_length=300)
    frequency: str | None = None
    actors: list[str] = Field(default_factory=list)
    volume: str | None = None
    evidence: list[CardEvidence] = Field(default_factory=list)  # empty when the text gives none


class CardStat(HuskModel):
    id: str  # S1, S2…
    sheet: str | None = None
    metric: str  # rows | date_range | cadence | empty_rate | top_values | duplicates | sample_row
    columns: list[str] = Field(default_factory=list)
    value: Any = None
    text: str = Field(..., max_length=300)
    cells: str | None = None


class DocumentCard(OrgScoped):
    document_id: str
    sha256: str
    version: int = CARD_VERSION
    kind: CardKind
    title: str
    sentence: str = Field(..., max_length=400)
    department: str | None = None
    doc_type: str | None = None
    signal: float = Field(0.0, ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    summary: str = Field("", max_length=1200)
    signals: dict[str, Any] = Field(default_factory=dict)  # rep, manual, freq, dept, vol, sys (+ rows, cadence… for sheets)
    quotes: list[CardEvidence] = Field(default_factory=list)  # key evidence (passages, or computed facts for sheets)
    activities: list[CardActivity] = Field(default_factory=list)
    stats: list[CardStat] = Field(default_factory=list)
    model: str | None = None  # None: computed locally (spreadsheets)
    created_at: datetime = Field(default_factory=utcnow)

    def evidence_for(self, ref: str) -> list[CardEvidence]:
        """Card ids → evidence: A* gives that activity's evidence, S* the computed fact."""
        for a in self.activities:
            if a.id == ref:
                return list(a.evidence)
        for s in self.stats:
            if s.id == ref:
                return [CardEvidence(document_id=self.document_id, stat_id=s.id, sheet=s.sheet, cells=s.cells,
                                     quote=s.text[:300])]
        return []

    def signal_text(self) -> str:
        """Signals as `k=v` pairs (token-cheap, not JSON)."""
        parts = []
        for k, v in self.signals.items():
            if v in (None, "", [], False):
                continue
            if v is True:
                v = "yes"
            elif isinstance(v, list):
                v = ",".join(str(x) for x in v)
            elif isinstance(v, float):
                v = f"{v:.2f}".rstrip("0").rstrip(".")
            parts.append(f"{k}={v}")
        return " ".join(parts)

    def line(self, alias: str | None = None) -> str:
        """One line per document: alias | title | sentence | signals."""
        return f"{alias or self.document_id} | {self.title} | {self.sentence} | {self.signal_text()}"

    def detail(self) -> str:
        """L1 view: activities and computed facts with their ids."""
        out = [self.line()]
        if self.department:
            out.append(f"  department: {self.department}")
        for a in self.activities:
            meta = ", ".join(x for x in (a.frequency, ", ".join(a.actors), a.volume) if x)
            ev = "; ".join(f"«{e.quote}»" for e in a.evidence if e.quote)[:300]
            out.append(f"  {a.id}: {a.what}" + (f" ({meta})" if meta else "") + (f" — evidence: {ev}" if ev else " — no evidence"))
        for s in self.stats:
            out.append(f"  {s.id}: {s.text}")
        return "\n".join(out)
