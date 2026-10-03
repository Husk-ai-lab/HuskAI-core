"""Heading-aware chunking.

Chunks follow the document's own structure (headings, paragraphs, tables). Tables are split
by rows and every piece repeats the header row, so a chunk is always readable on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TARGET = 1400
HARD_MAX = 2400


@dataclass
class RawChunk:
    heading: str | None
    text: str
    start: int
    end: int


def _blocks(markdown: str) -> list[tuple[str | None, str, int, int, bool]]:
    """(heading, block_text, start, end, is_table) in document order."""
    blocks = []
    heading = None
    pos = 0
    buf: list[str] = []
    buf_start = 0
    in_table = False

    def flush(end: int) -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            blocks.append((heading, text, buf_start, end, in_table))
        buf = []

    for line in markdown.splitlines(keepends=True):
        stripped = line.strip()
        m = HEADING.match(stripped)
        is_table_line = stripped.startswith("|")
        if m:
            flush(pos)
            heading = m.group(2).strip()[:200]
            in_table = False
            buf_start = pos + len(line)
        elif not stripped:
            flush(pos)
            in_table = False
            buf_start = pos + len(line)
        else:
            if is_table_line != in_table and buf:
                flush(pos)
                buf_start = pos
            in_table = is_table_line
            if not buf:
                buf_start = pos
            buf.append(line.rstrip("\n"))
        pos += len(line)
    flush(pos)
    return blocks


def _split_table(text: str) -> list[str]:
    lines = text.splitlines()
    if len(text) <= HARD_MAX or len(lines) < 4:
        return [text]
    header = lines[:2] if set(lines[1].replace("|", "").strip()) <= set("-: ") else lines[:1]
    body = lines[len(header) :]
    pieces, cur = [], list(header)
    for row in body:
        if sum(len(x) for x in cur) + len(row) > TARGET and len(cur) > len(header):
            pieces.append("\n".join(cur))
            cur = list(header)
        cur.append(row)
    if len(cur) > len(header):
        pieces.append("\n".join(cur))
    return pieces


def _split_prose(text: str) -> list[str]:
    if len(text) <= HARD_MAX:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) > TARGET and cur:
            pieces.append(cur.strip())
            cur = ""
        cur += s + " "
    if cur.strip():
        pieces.append(cur.strip())
    return pieces


def chunk_markdown(markdown: str) -> list[RawChunk]:
    chunks: list[RawChunk] = []
    cur_heading: str | None = None
    cur_parts: list[str] = []
    cur_start = 0
    cur_end = 0

    def emit() -> None:
        nonlocal cur_parts
        if cur_parts:
            chunks.append(RawChunk(cur_heading, "\n\n".join(cur_parts), cur_start, cur_end))
        cur_parts = []

    for heading, text, start, end, is_table in _blocks(markdown):
        pieces = _split_table(text) if is_table else _split_prose(text)
        for piece in pieces:
            if heading != cur_heading or (cur_parts and sum(map(len, cur_parts)) + len(piece) > TARGET):
                emit()
                cur_heading = heading
                cur_start = start
            if not cur_parts:
                cur_start = start
            cur_parts.append(piece)
            cur_end = end
    emit()
    return chunks
