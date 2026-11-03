"""Split extracted pages into overlapping chunks with section metadata.

Target size is about 1,200 characters and overlap is about 200. The split
prefers paragraph boundaries, then sentences, then a hard cut. Overlap keeps a
clause that sits on a boundary inside at least one chunk.

Each chunk records the last clause heading seen in its text (``CLAUSE``,
``ARTICLE``, ``ENDORSEMENT``). Ingest prepends that heading when the body does not
already contain it, so both the embedding and the full-text index can find the
clause by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.extraction import PageText

HEADING_RE = re.compile(
    r"^(CLAUSE\s+\d+[^\n]*|ARTICLE\s+\d+[^\n]*|ENDORSEMENT\b[^\n]*)",
    re.IGNORECASE,
)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk before it has an id, a vector, or a row in the database."""

    chunk_index: int
    body: str
    page_start: int
    page_end: int
    section_title: str | None


def chunk_pages(
    pages: list[PageText],
    target_chars: int = 1200,
    overlap_chars: int = 200,
) -> list[ChunkDraft]:
    """Pack pages into overlapping chunks.

    ``overlap_chars`` must be smaller than ``target_chars``. Otherwise the
    window would not move forward and the loop would repeat the same text.
    """
    if overlap_chars >= target_chars:
        raise ValueError("overlap must be smaller than the target chunk size")
    paragraphs = _paragraphs(pages, target_chars, overlap_chars)
    if not paragraphs:
        return []

    chunks: list[ChunkDraft] = []
    current: list[tuple[int, str, str | None]] = []
    current_len = 0

    for item in paragraphs:
        _page, text, _section = item
        size = len(text) + 2
        if current and current_len + size > target_chars:
            chunks.append(_to_draft(chunks, current))
            current = _overlap_tail(current, overlap_chars)
            current_len = sum(len(text) + 2 for _page, text, _section in current)
        current.append(item)
        current_len += size
    if current:
        chunks.append(_to_draft(chunks, current))
    return chunks


def _paragraphs(
    pages: list[PageText],
    target_chars: int,
    overlap_chars: int,
) -> list[tuple[int, str, str | None]]:
    """Flatten pages into paragraphs tagged with the heading in force."""
    items: list[tuple[int, str, str | None]] = []
    section: str | None = None
    for page in pages:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", page.text) if block.strip()]
        if not blocks and page.text.strip():
            blocks = [page.text.strip()]
        for block in blocks:
            for piece in _split_long(block, target_chars, overlap_chars):
                heading = _heading_of(piece)
                if heading:
                    section = heading
                items.append((page.number, piece, section))
    return items


def _split_long(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    """Break one oversized paragraph into pieces that fit the target."""
    if len(text) <= target_chars:
        return [text]
    sentences = [part.strip() for part in SENTENCE_RE.split(text) if part.strip()]
    if len(sentences) <= 1:
        return _hard_split(text, target_chars, overlap_chars)
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(sentence) > target_chars:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.extend(_hard_split(sentence, target_chars, overlap_chars))
            continue
        candidate = f"{buffer} {sentence}".strip()
        if buffer and len(candidate) > target_chars:
            pieces.append(buffer)
            buffer = sentence
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    return pieces


def _hard_split(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    """Cut a single long run of text, stepping back by the overlap each time."""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + target_chars)
        pieces.append(text[start:end].strip())
        if end == len(text):
            break
        start = end - overlap_chars
    return [piece for piece in pieces if piece]


def _overlap_tail(
    paragraphs: list[tuple[int, str, str | None]],
    overlap_chars: int,
) -> list[tuple[int, str, str | None]]:
    """Keep the tail of a finished chunk, but always drop at least the first paragraph.

    Dropping the first paragraph is what makes the next chunk start later in
    the document. Without that, a short chunk would be copied forever.
    """
    if len(paragraphs) <= 1:
        return []
    tail: list[tuple[int, str, str | None]] = []
    total = 0
    for item in reversed(paragraphs):
        if tail and total >= overlap_chars:
            break
        if len(tail) + 1 >= len(paragraphs):
            break
        tail.append(item)
        total += len(item[1])
    tail.reverse()
    return tail


def _to_draft(
    existing: list[ChunkDraft],
    paragraphs: list[tuple[int, str, str | None]],
) -> ChunkDraft:
    """Build one draft. The section is the heading in force at the last paragraph."""
    body = "\n\n".join(text for _page, text, _section in paragraphs)
    section = paragraphs[-1][2]
    return ChunkDraft(
        chunk_index=len(existing),
        body=body,
        page_start=paragraphs[0][0],
        page_end=paragraphs[-1][0],
        section_title=section,
    )


def _heading_of(text: str) -> str | None:
    """Return the clause heading this paragraph starts, if it starts one."""
    first_line = text.strip().splitlines()[0].strip()
    match = HEADING_RE.match(first_line)
    if not match:
        return None
    return match.group(1).strip()[:160]


def body_for_index(draft: ChunkDraft) -> str:
    """Text that is embedded and stored.

    The heading is prepended when the chunk is a continuation of a clause whose
    title sat in the previous chunk. Citations then still show the clause name.
    """
    if draft.section_title and draft.section_title not in draft.body:
        return f"{draft.section_title}\n\n{draft.body}"
    return draft.body
