"""Hybrid retrieval for a single claim.

Vector search and English full-text search each return a ranked list. Reciprocal
Rank Fusion combines those ranks. It does not add the raw scores: cosine
similarity and ``ts_rank_cd`` are not on the same scale, so a weighted sum would
need a calibration set we do not have.

Every query filters ``chunks.claim_id``. There is no search function that omits
that filter.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Chunk, Document

TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "what",
    "who",
    "how",
    "when",
    "where",
    "which",
    "are",
    "was",
    "were",
    "has",
    "have",
    "not",
    "but",
    "its",
    "their",
    "does",
    "did",
    "can",
    "about",
    "into",
    "than",
    "then",
    "also",
    "any",
    "all",
    "per",
    "our",
    "you",
    "your",
}


@dataclass(frozen=True)
class RetrievedChunk:
    """One fused hit, with the metadata a citation needs."""

    chunk_id: str
    document_id: str
    claim_id: str
    filename: str
    version: int
    document_type: str
    document_date: str | None
    page_start: int
    page_end: int
    section_title: str | None
    body: str
    fused_score: float
    cosine: float | None


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Fuse ranked id lists. Rank 1 adds ``1 / (k + 1)``. Ties break by id.

    ``k = 60`` is the usual default from the original fusion paper. It keeps a
    single first place from dominating the rest of the list.
    """
    if k < 1:
        raise ValueError("k must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def hybrid_search(
    session: Session,
    claim_id: str,
    question: str,
    query_vector: list[float],
    *,
    candidates: int = 20,
    limit: int = 5,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """Return the top fused chunks for this claim only, best first."""
    if not question.strip():
        return []
    vector_hits = _vector_search(session, claim_id, query_vector, candidates)
    lexical_ids = _lexical_search(session, claim_id, question, candidates)
    fused = reciprocal_rank_fusion(
        [[chunk_id for chunk_id, _cosine in vector_hits], lexical_ids],
        k=rrf_k,
    )
    if not fused:
        return []
    cosine_by_id = dict(vector_hits)
    ordered_ids = [chunk_id for chunk_id, _score in fused[:limit]]
    score_by_id = dict(fused)
    rows = session.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.id.in_(ordered_ids),
            Chunk.claim_id == claim_id,
            Chunk.active.is_(True),
            Document.active.is_(True),
        )
    ).all()
    by_id = {chunk.id: (chunk, document) for chunk, document in rows}
    results: list[RetrievedChunk] = []
    for chunk_id in ordered_ids:
        pair = by_id.get(chunk_id)
        if pair is None:
            continue
        chunk, document = pair
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                claim_id=claim_id,
                filename=document.filename,
                version=document.version,
                document_type=document.document_type,
                document_date=document.document_date,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_title=chunk.section_title,
                body=chunk.body,
                fused_score=score_by_id[chunk_id],
                cosine=cosine_by_id.get(chunk_id),
            )
        )
    return results


def evidence_is_sufficient(question: str, hits: list[RetrievedChunk]) -> bool:
    """True when the top hit actually shares content words with the question.

    A fused score alone is not enough. With a hashing embedder, some vector is
    always the nearest neighbour, even when the claim says nothing about the
    question. Token overlap is the gate that makes the assistant refuse.
    """
    query_tokens = significant_tokens(question)
    if not hits or not query_tokens:
        return False
    top_tokens = significant_tokens(hits[0].body)
    overlap = len(query_tokens & top_tokens) / len(query_tokens)
    if overlap >= 0.2:
        return True
    cosine = hits[0].cosine or 0.0
    return cosine >= 0.55 and overlap >= 0.1


def significant_tokens(text: str) -> set[str]:
    """Content tokens only: folded, at least 3 characters, not a Spanish stop word."""
    folded = _fold(text)
    return {token for token in TOKEN_RE.findall(folded) if token not in STOPWORDS}


def _vector_search(
    session: Session,
    claim_id: str,
    query_vector: list[float],
    candidates: int,
) -> list[tuple[str, float]]:
    """Nearest active chunks of this claim by cosine distance."""
    distance = Chunk.embedding.cosine_distance(query_vector)
    rows = session.execute(
        select(Chunk.id, (1 - distance).label("cosine"))
        .where(Chunk.claim_id == claim_id, Chunk.active.is_(True))
        .order_by(distance)
        .limit(candidates)
    ).all()
    return [(row.id, float(row.cosine)) for row in rows]


def _lexical_search(session: Session, claim_id: str, question: str, candidates: int) -> list[str]:
    """English full-text search. An empty tsquery yields no rows instead of an error."""
    query = func.plainto_tsquery("english", question)
    rank = func.ts_rank_cd(Chunk.search_vector, query)
    try:
        rows = session.execute(
            select(Chunk.id)
            .where(
                Chunk.claim_id == claim_id,
                Chunk.active.is_(True),
                Chunk.search_vector.op("@@")(query),
            )
            .order_by(rank.desc(), Chunk.id)
            .limit(candidates)
        ).all()
    except SQLAlchemyError:
        # A question that is only stop words can make plainto_tsquery fail.
        # Lexical search then contributes nothing; vector search still stands.
        session.rollback()
        return []
    return [row.id for row in rows]


def _fold(text: str) -> str:
    """Lowercase and remove accents so the overlap gate is not tripped by orthography."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(character for character in decomposed if not unicodedata.combining(character))
