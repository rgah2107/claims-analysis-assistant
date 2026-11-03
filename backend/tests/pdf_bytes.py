"""Tiny PDF writer for tests.

The files are real enough for pypdf: a Helvetica font, one content stream per
page, and a correct xref. Text is WinAnsi, with non-ASCII bytes escaped, so a
Spanish sentence survives extraction.
"""

from __future__ import annotations


def build_pdf(pages: list[str]) -> bytes:
    """Return a multi-page PDF whose extractable text is ``pages``."""
    streams = [_content_stream(page) for page in pages]
    page_ids = [3 + (index * 2) for index in range(len(streams))]
    content_ids = [page_id + 1 for page_id in page_ids]
    font_id = 3 + (len(streams) * 2)
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: list[bytes] = [
        _obj(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        _obj(2, f"<< /Type /Pages /Count {len(streams)} /Kids [{kids}] >>".encode("ascii")),
    ]
    for index, stream in enumerate(streams):
        objects.append(
            _obj(
                page_ids[index],
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {content_ids[index]} 0 R "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
                ).encode("ascii"),
            )
        )
        objects.append(
            _obj(
                content_ids[index],
                f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
            )
        )
    objects.append(_obj(font_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    return _assemble(objects)


def _content_stream(page: str) -> bytes:
    """One page of text, top to bottom, one PDF line per input line."""
    commands = ["BT", "/F1 12 Tf", "72 740 Td", "16 TL"]
    for line in page.split("\n"):
        commands.append(f"({_escape(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    return "\n".join(commands).encode("ascii")


def _escape(text: str) -> str:
    """Escape a PDF literal string using Latin-1 octal for accents."""
    raw = text.encode("latin-1", errors="replace")
    pieces: list[str] = []
    for byte in raw:
        if byte in (0x28, 0x29, 0x5C):
            pieces.append("\\" + chr(byte))
        elif 32 <= byte < 127:
            pieces.append(chr(byte))
        else:
            pieces.append(f"\\{byte:03o}")
    return "".join(pieces)


def _obj(number: int, body: bytes) -> bytes:
    """One indirect object."""
    return f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"


def _assemble(objects: list[bytes]) -> bytes:
    """Write the header, the objects, and a classic xref table."""
    blob = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(blob))
        blob += obj
    startxref = len(blob)
    size = len(objects) + 1
    xref = [f"xref\n0 {size}\n", "0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n" for offset in offsets)
    blob += "".join(xref).encode("ascii")
    blob += (
        f"trailer << /Size {size} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode("ascii")
    )
    return blob
