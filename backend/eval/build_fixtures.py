"""Write the synthetic policy PDF and the endorsement DOCX.

Run from the backend directory:

    python -m eval.build_fixtures
"""

from __future__ import annotations

import io
from pathlib import Path

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from eval.corpus import LETTER_PARAGRAPHS, POLICY_PAGES

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def build() -> None:
    """Regenerate both files. The evaluation script calls this if they are missing."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    _write_pdf(FIXTURES / "home-policy.pdf", POLICY_PAGES)
    _write_docx(FIXTURES / "endorsement-clause-4.docx", LETTER_PARAGRAPHS)


def _write_pdf(path: Path, pages: list[str]) -> None:
    """One canvas page per list entry. Helvetica covers the Latin-1 characters we use."""
    drawing = canvas.Canvas(str(path), pagesize=A4)
    for page in pages:
        y = 800
        for line in page.split("\n"):
            drawing.drawString(72, y, line)
            y -= 18
        drawing.showPage()
    drawing.save()


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    """A one-page letter. python-docx stores each string as its own paragraph."""
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    path.write_bytes(buffer.getvalue())


if __name__ == "__main__":
    build()
    print(f"wrote fixtures in {FIXTURES}")
