"""Chunk size, overlap, page span, and clause headings."""

from __future__ import annotations

import pytest
from app.chunking import chunk_pages
from app.extraction import PageText


def test_short_document_is_one_chunk_spanning_its_pages() -> None:
    """Two short pages fit in one chunk and keep both page numbers."""
    chunks = chunk_pages(
        [PageText(1, "First page of the contract."), PageText(2, "Second page of the contract.")]
    )
    assert len(chunks) == 1
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2
    assert chunks[0].chunk_index == 0


def test_heading_becomes_the_section_title() -> None:
    """A clause heading is metadata, not only text the search might miss."""
    chunks = chunk_pages(
        [PageText(1, "CLAUSE 4. Water damage\n\nThe limit is 2000 EUR.")]
    )
    assert chunks[0].section_title is not None
    assert chunks[0].section_title.startswith("CLAUSE 4")


def test_overlap_repeats_the_boundary_paragraph_and_still_advances() -> None:
    """The next chunk starts with the tail of the previous one, then new text."""
    # Short enough that one chunk holds several paragraphs, so the overlap
    # can keep the tail without copying the whole chunk.
    paragraphs = [f"Block {index}. " + ("text " * 12) for index in range(12)]
    chunks = chunk_pages([PageText(1, "\n\n".join(paragraphs))], target_chars=400, overlap_chars=90)
    assert len(chunks) > 2
    boundary = chunks[0].body.split("\n\n")[-1]
    assert boundary in chunks[1].body
    assert chunks[1].body != chunks[0].body
    assert chunks[-1].chunk_index == len(chunks) - 1


def test_overlap_must_be_smaller_than_the_target() -> None:
    """An overlap as large as the target would repeat the same window forever."""
    with pytest.raises(ValueError):
        chunk_pages([PageText(1, "texto")], target_chars=100, overlap_chars=100)


def test_empty_input_produces_no_chunks() -> None:
    """A blank extraction is the caller's problem; the chunker does not invent text."""
    assert chunk_pages([PageText(1, "   \n\n  ")]) == []
