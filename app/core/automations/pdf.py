"""Minimal dependency-free PDF writer for text reports (Helvetica, A4, multi-page)."""

from __future__ import annotations

import textwrap
from pathlib import Path


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, title: str, body: str) -> Path:
    lines: list[tuple[str, int]] = [(title, 16), ("", 11)]
    for para in body.splitlines() or [""]:
        wrapped = textwrap.wrap(para, 92) or [""]
        lines += [(w, 11) for w in wrapped]
    per_page = 52
    pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[("", 11)]]
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    pages_idx = add(b"")  # placeholder
    page_ids = []
    for page in pages:
        y = 800
        ops = []
        for text, size in page:
            ops.append(f"BT /F1 {size} Tf 50 {y} Td ({_esc(text)}) Tj ET")
            y -= size + 4
        stream = "\n".join(ops).encode("cp1252", errors="replace")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R >> >> "
            b"/Contents %d 0 R >>" % (pages_idx, font, content)
        ))
    objs[pages_idx - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % p for p in page_ids), len(page_ids))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_idx)
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
