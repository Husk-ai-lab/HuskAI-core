"""Tools shared by the internal agents (explorer and builder).

Three families: knowledge (BM25 RAG), data-scientist tools (CSV/Excel/DB) and memory
(company profile + network notes). All are bound to one AgentContext (one org).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.core.agents.harness.context import AgentContext
from app.core.knowledge.memory.notes import NoteInvalid
from app.data.models.knowledge import EvidenceRef, FactCandidate, FactCategory, NoteKind
from app.helpers.text import truncate


# --- argument schemas ----------------------------------------------------------------------
class SearchArgs(BaseModel):
    query: str = Field(..., description="keywords to search for in the company documents")
    k: int = Field(6, ge=1, le=15)
    document_ids: list[str] | None = Field(None, description="restrict the search to these documents")


class ChunkArgs(BaseModel):
    chunk_id: str


class DocArgs(BaseModel):
    document_id: str
    part: int = Field(0, ge=0, description="part of the document (0 = start), 4000 characters per part")


class SheetArgs(BaseModel):
    document_id: str
    sheet: str | None = None


class FilterArgs(SheetArgs):
    column: str
    op: str = Field(..., description="==, !=, >, >=, <, <=, contains, isnull, notnull")
    value: Any = None
    limit: int = 20


class AggregateArgs(SheetArgs):
    column: str
    func: str = Field("sum", description="sum, mean, min, max, count, median, nunique")
    group_by: str | None = None


class ValueCountsArgs(SheetArgs):
    column: str


class PeriodicityArgs(SheetArgs):
    date_column: str | None = None


class CompareArgs(BaseModel):
    document_a: str
    document_b: str
    key_column: str
    sheet_a: str | None = None
    sheet_b: str | None = None


class ReadCellsArgs(SheetArgs):
    start_row: int = 0
    n_rows: int = 20
    columns: list[str] | None = None


class EvidenceArg(BaseModel):
    document_id: str
    chunk_id: str | None = None
    sheet: str | None = None
    quote: str | None = Field(None, description="short verbatim quote (max 300 characters)")


class WriteNoteArgs(BaseModel):
    network_id: str
    title: str
    claim: str = Field(..., description="COMPLETE, verified information, full sentences, in the user's language")
    kind: NoteKind = NoteKind.task
    frequency: str | None = Field(None, description="e.g. 'every evening', 'at every new revision'")
    actors: list[str] = Field(default_factory=list, description="departments or roles, NEVER people's names")
    evidence: list[EvidenceArg] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list, description="ids of related notes")
    hours_per_week: float | None = None
    note_id: str | None = Field(None, description="to rewrite an existing note (always in full)")


class NetworkArgs(BaseModel):
    network_id: str


class NoteArgs(BaseModel):
    note_id: str


class LinkArgs(BaseModel):
    note_a: str
    note_b: str


class FactArgs(BaseModel):
    text: str = Field(..., description="a stable, useful fact about the company, max 300 characters, in the user's language")
    category: FactCategory = FactCategory.process
    importance: float = Field(0.5, ge=0, le=1)
    document_id: str | None = None
    quote: str | None = None


class NoArgs(BaseModel):
    pass


class DocIdArgs(BaseModel):
    document_id: str


# --- builders --------------------------------------------------------------------------------
def knowledge_tools(ctx: AgentContext) -> list[BaseTool]:
    ks = ctx.knowledge

    def search_knowledge(query: str, k: int = 6, document_ids: list[str] | None = None) -> list[dict]:
        return [
            {"chunk_id": h.chunk_id, "document_id": h.document_id, "heading": h.heading,
             "score": h.score, "text": truncate(h.text, 900)}
            for h in ks.search_chunks(query, k=k, document_ids=document_ids)
        ]

    def read_chunk(chunk_id: str) -> dict:
        ch = ks.docs.chunk(chunk_id)
        if not ch:
            raise ValueError("chunk does not exist")
        return {"chunk_id": ch.id, "document_id": ch.document_id, "heading": ch.heading, "text": ch.text}

    def read_document(document_id: str, part: int = 0) -> dict:
        doc = ks.docs.get(document_id)
        if not doc:
            raise ValueError("document does not exist")
        md = ks.docs.markdown(document_id)
        size = 4000
        return {
            "document_id": doc.id, "filename": doc.filename, "part": part,
            "parts": max(1, -(-len(md) // size)), "text": md[part * size : (part + 1) * size],
        }

    def list_documents() -> list[dict]:
        return [
            {"document_id": d.id, "filename": d.filename, "kind": d.kind.value, "department": d.department,
             "rows": d.rows}
            for d in ks.docs.list() if d.status.value == "ingested"
        ]

    return [
        StructuredTool.from_function(search_knowledge, name="search_knowledge", args_schema=SearchArgs,
                                     description="BM25 search over the company documents: returns the most relevant passages."),
        StructuredTool.from_function(read_chunk, name="read_chunk", args_schema=ChunkArgs,
                                     description="Read a document passage (chunk) in full."),
        StructuredTool.from_function(read_document, name="read_document", args_schema=DocArgs,
                                     description="Read a document in parts of 4000 characters."),
        StructuredTool.from_function(list_documents, name="list_documents", args_schema=NoArgs,
                                     description="List the authorised documents in the knowledge base."),
    ]


def data_tools(ctx: AgentContext) -> list[BaseTool]:
    t = ctx.knowledge.tabular()
    spec = [
        ("list_sheets", t.list_sheets, DocIdArgs, "List the sheets or tables of a data file (Excel, CSV, DB)."),
        ("describe_table", t.describe_table, SheetArgs, "Table structure: columns, types, missing values, examples."),
        ("filter_rows", t.filter_rows, FilterArgs, "Filter the rows of a table with a condition."),
        ("aggregate", t.aggregate, AggregateArgs, "Sum/mean/count of a column, optionally grouped."),
        ("value_counts", t.value_counts, ValueCountsArgs, "Value counts of a column."),
        ("detect_periodicity", t.detect_periodicity, PeriodicityArgs,
         "Detect whether a table grows on a regular cadence (daily, weekly): a signal of repetitive work."),
        ("compare_versions", t.compare_versions, CompareArgs, "Compare two tables (e.g. revisions of a bill of materials)."),
        ("missing_values", t.missing_values, SheetArgs, "Missing values per column (data to be filled in by hand)."),
        ("read_cells", t.read_cells, ReadCellsArgs, "Read a range of rows of a table."),
    ]
    tools = []
    for name, fn, schema, desc in spec:
        tools.append(StructuredTool.from_function(fn, name=name, args_schema=schema, description=desc))
    return tools


def memory_read_tools(ctx: AgentContext) -> list[BaseTool]:
    mem = ctx.memory

    def read_company_profile() -> str:
        return mem.profile.markdown(ctx.language)

    def read_network(network_id: str) -> str:
        net = mem.networks.get(network_id)
        if not net:
            raise ValueError("network does not exist")
        return mem.networks.render(net, ctx.language)

    def read_note(note_id: str) -> str:
        note = mem.repo.note(note_id)
        if not note:
            raise ValueError("note does not exist")
        return note.render_markdown(ctx.language)

    def propose_fact(**_: object) -> dict:
        raise RuntimeError("propose_fact is async-only")

    async def apropose_fact(text: str, category: FactCategory = FactCategory.process, importance: float = 0.5,
                            document_id: str | None = None, quote: str | None = None) -> dict:
        cand = FactCandidate(
            text=text, category=category, importance=importance,
            evidence=[EvidenceRef(document_id=document_id, quote=quote)] if document_id else [],
        )
        outcome = await mem.profile.add(cand)
        return {"decision": outcome.decision.value, "reason": outcome.reason,
                "replaced": outcome.replaced.text if outcome.replaced else None}

    return [
        StructuredTool.from_function(read_company_profile, name="read_company_profile", args_schema=NoArgs,
                                     description="Company profile (at most 30 key facts)."),
        StructuredTool.from_function(read_network, name="read_network", args_schema=NetworkArgs,
                                     description="Read all the notes of a knowledge network."),
        StructuredTool.from_function(read_note, name="read_note", args_schema=NoteArgs,
                                     description="Read a single note."),
        StructuredTool.from_function(propose_fact, coroutine=apropose_fact, name="propose_fact", args_schema=FactArgs,
                                     description="Propose a fact for the company profile (max 30: it replaces a less important fact only if it is worth more)."),
    ]


def memory_write_tools(ctx: AgentContext) -> list[BaseTool]:
    nets = ctx.memory.networks

    def write_note(network_id: str, title: str, claim: str, kind: NoteKind = NoteKind.task,
                   frequency: str | None = None, actors: list[str] | None = None,
                   evidence: list[EvidenceArg] | None = None, links: list[str] | None = None,
                   hours_per_week: float | None = None, note_id: str | None = None) -> dict:
        ev = [EvidenceRef(**(e.model_dump() if isinstance(e, BaseModel) else e)) for e in (evidence or [])]
        try:
            note = nets.write_note(
                network_id, title=title, claim=claim, kind=kind, frequency=frequency, actors=actors,
                evidence=ev, links=links, hours_per_week=hours_per_week, note_id=note_id,
            )
        except NoteInvalid as exc:
            raise ValueError("note NOT saved, rewrite it in full: " + "; ".join(exc.errors)) from exc
        return {"note_id": note.id, "version": note.version, "links": note.links}

    def link_notes(note_a: str, note_b: str) -> str:
        nets.link(note_a, note_b)
        return "linked"

    return [
        StructuredTool.from_function(write_note, name="write_note", args_schema=WriteNoteArgs,
                                     description="Create or rewrite IN FULL a network note. Every note must contain complete information with evidence."),
        StructuredTool.from_function(link_notes, name="link_notes", args_schema=LinkArgs,
                                     description="Link two notes that describe connected activities across documents."),
    ]
