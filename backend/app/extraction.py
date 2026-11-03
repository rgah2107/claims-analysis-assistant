"""Turn a PDF or a DOCX into pages of text.

Docling is the tool the brief names for layout-aware extraction. This module
uses pypdf and python-docx instead. They keep page numbers, they do not
download a model, and every branch is short enough to walk through in an
interview. A scanned PDF has no text layer: that is a permanent failure with
an explicit reason, not a silent empty document.

DOCX has no reliable page number. A page break starts a new page. A letter
without a page break is page 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.errors import PermanentIngestError

DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


@dataclass(frozen=True)
class PageText:
    """One page, numbered from 1, as the citation will report it."""

    number: int
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    """Everything ingest needs before chunking."""

    pages: list[PageText]
    document_type: str
    document_date: str | None


def extract_file(path: Path, filename: str) -> ExtractedDocument:
    """Read ``path`` and classify it. Raises ``PermanentIngestError`` on a bad file."""
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        pages = _extract_pdf(path)
    elif lowered.endswith(".docx"):
        pages = _extract_docx(path)
    else:
        raise PermanentIngestError("Unsupported file type.")
    if not any(page.text.strip() for page in pages):
        raise PermanentIngestError(
            "No extractable text. Scanned PDFs are not supported in this version."
        )
    full_text = "\n".join(page.text for page in pages)
    return ExtractedDocument(
        pages=pages,
        document_type=_infer_type(filename, full_text),
        document_date=_infer_date(full_text),
    )


def _extract_pdf(path: Path) -> list[PageText]:
    """Read each PDF page. Encrypted or unreadable files fail permanently."""
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise PermanentIngestError("The PDF is encrypted.")
        pages = [
            PageText(number=index, text=page.extract_text() or "")
            for index, page in enumerate(reader.pages, start=1)
        ]
    except PermanentIngestError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise PermanentIngestError(f"Corrupt PDF: {exc}") from exc
    if not pages:
        raise PermanentIngestError("The PDF has no pages.")
    return pages


def _extract_docx(path: Path) -> list[PageText]:
    """Read paragraphs, starting a new page when Word stored a page break."""
    try:
        document = Document(str(path))
    except (OSError, ValueError) as exc:
        raise PermanentIngestError(f"Corrupt DOCX: {exc}") from exc
    pages: list[list[str]] = [[]]
    for paragraph in document.paragraphs:
        if _has_page_break(paragraph) and any(pages[-1]):
            pages.append([])
        text = paragraph.text.strip()
        if text:
            pages[-1].append(text)
    rendered = [PageText(number=index, text="\n".join(lines)) for index, lines in enumerate(pages, start=1)]
    return [page for page in rendered if page.text.strip()] or rendered


def _has_page_break(paragraph) -> bool:
    """True when the paragraph contains a Word page break."""
    breaks = paragraph._p.findall(".//" + qn("w:br"))
    return any(node.get(qn("w:type")) == "page" for node in breaks)


def _infer_type(filename: str, text: str) -> str:
    """Classify from the file name first, then from the opening of the text."""
    name = filename.lower()
    if any(token in name for token in ("endorsement", "letter")):
        return "letter"
    if "survey" in name or "report" in name:
        return "report"
    if "policy" in name:
        return "policy"
    opening = text[:600].lower()
    if "policy" in opening:
        return "policy"
    return "unknown"


def _infer_date(text: str) -> str | None:
    """Return the first dd/mm/yyyy as ISO, which is the document's own date."""
    match = DATE_RE.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"
