"""PDF and DOCX extraction, including the failures the brief names."""

from __future__ import annotations

from app.errors import PermanentIngestError
from app.extraction import extract_file
from docx import Document
from docx.enum.text import WD_BREAK
from pypdf import PdfWriter
from tests.pdf_bytes import build_pdf


def test_pdf_keeps_page_numbers_and_policy_text(tmp_path) -> None:
    """Page 2 must stay page 2, because citations name a page."""
    path = tmp_path / "home-policy.pdf"
    path.write_bytes(
        build_pdf(
            [
                "Policy number: POL-NS-2024-1187.",
                "Damage caused by lack of maintenance is not covered.",
            ]
        )
    )
    extracted = extract_file(path, "home-policy.pdf")
    assert extracted.document_type == "policy"
    assert extracted.pages[0].number == 1
    assert "POL-NS-2024-1187" in extracted.pages[0].text
    assert extracted.pages[1].number == 2
    assert "maintenance" in extracted.pages[1].text


def test_corrupt_pdf_is_permanent(tmp_path) -> None:
    """The magic byte was accepted at upload; the parser still has to fail clearly."""
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a valid pdf")
    try:
        extract_file(path, "broken.pdf")
    except PermanentIngestError as exc:
        assert "PDF" in str(exc) or "text" in str(exc)
    else:
        raise AssertionError("a broken PDF must not ingest")


def test_blank_pdf_explains_that_scans_are_unsupported(tmp_path) -> None:
    """No text layer is a different failure from a corrupt file."""
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)
    try:
        extract_file(path, "blank.pdf")
    except PermanentIngestError as exc:
        assert "Scanned PDFs" in str(exc)
    else:
        raise AssertionError("a blank PDF must fail")


def test_docx_page_break_and_letter_classification(tmp_path) -> None:
    """A Word page break starts a cited page. The file name classifies the letter."""
    document = Document()
    document.add_paragraph("Endorsement dated 15/06/2024.")
    breaker = document.add_paragraph()
    breaker.add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("The deductible does not change.")
    path = tmp_path / "endorsement-clause.docx"
    document.save(path)
    extracted = extract_file(path, path.name)
    assert extracted.document_type == "letter"
    assert extracted.document_date == "2024-06-15"
    assert [page.number for page in extracted.pages] == [1, 2]
    assert "deductible" in extracted.pages[1].text
