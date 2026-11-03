"""Ingest one stored document.

Stages are committed separately so the document list can show extracting,
chunking, embedding, and indexing while the work is still running. Chunks are
replaced in one transaction at the end. A retry that dies before that commit
leaves no partial fragments, and a second successful run deletes the previous
fragments for this document before inserting the new ones.

A document that is already ``ready`` returns immediately. Uploading the same
bytes does not call this function again.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.chunking import body_for_index, chunk_pages
from app.config import get_settings
from app.db import session_scope
from app.embeddings import get_embedding_provider
from app.errors import PermanentIngestError, ProviderError, TransientIngestError
from app.extraction import extract_file
from app.models import EMBEDDING_DIMENSION, Chunk, Document
from app.observability import get_logger, start_span
from app.security import redact

logger = get_logger()


def run_ingest(document_id: str) -> None:
    """Extract, chunk, embed, and index one document. Safe to call more than once."""
    with start_span("ingest.document", **{"document.id": document_id}):
        path, filename, claim_id = _begin(document_id)
        if path is None:
            return
        extracted = extract_file(Path(path), filename)
        _set_status(document_id, "chunking")
        settings = get_settings()
        drafts = chunk_pages(
            extracted.pages,
            target_chars=settings.chunk_target_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )
        if not drafts:
            raise PermanentIngestError("The document produced no chunks.")
        bodies = [body_for_index(draft) for draft in drafts]
        _set_status(document_id, "embedding")
        try:
            with start_span("ingest.embed", **{"document.id": document_id, "chunk.count": len(bodies)}):
                vectors = get_embedding_provider().embed(bodies)
        except ProviderError as exc:
            raise TransientIngestError(str(exc)) from exc
        if any(len(vector) != EMBEDDING_DIMENSION for vector in vectors):
            raise PermanentIngestError(
                f"The embedding does not have {EMBEDDING_DIMENSION} dimensions."
            )
        _set_status(document_id, "indexing")
        _replace_chunks(
            document_id=document_id,
            claim_id=claim_id,
            drafts=drafts,
            bodies=bodies,
            vectors=vectors,
            document_type=extracted.document_type,
            document_date=extracted.document_date,
        )


def mark_failed(document_id: str, message: str) -> None:
    """Record a terminal failure. A document that already succeeded is left alone."""
    safe_message = redact(message)[:500]
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None or document.status == "ready":
            return
        document.status = "failed"
        document.error_message = safe_message
    logger.info("document %s failed: %s", document_id, safe_message)


def _begin(document_id: str) -> tuple[str | None, str, str]:
    """Mark the document extracting, or return no path when there is nothing to do.

    Nothing to do means the row is missing, inactive, or already ready. The
    ready check is the idempotent no-op for a duplicate delivery of the task.
    """
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None or not document.active or document.status == "ready":
            return None, "", ""
        document.status = "extracting"
        document.error_message = None
        return document.storage_path, document.filename, document.claim_id


def _set_status(document_id: str, status: str) -> None:
    """Commit one progress update. The UI polls these values."""
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None or document.status in {"ready", "failed"}:
            return
        document.status = status


def _replace_chunks(
    *,
    document_id: str,
    claim_id: str,
    drafts,
    bodies: list[str],
    vectors: list[list[float]],
    document_type: str,
    document_date: str | None,
) -> None:
    """Delete this document's chunks and insert the new set in one transaction.

    ``SELECT ... FOR UPDATE`` serialises two workers that picked up the same id.
    The second one sees ``ready`` and returns, so it does not double-insert.
    """
    with session_scope() as session:
        document = session.get(Document, document_id, with_for_update=True)
        if document is None:
            return
        if document.status == "ready":
            return
        session.query(Chunk).filter(Chunk.document_id == document_id).delete(
            synchronize_session=False
        )
        for draft, body, vector in zip(drafts, bodies, vectors, strict=True):
            session.add(
                Chunk(
                    document_id=document_id,
                    claim_id=claim_id,
                    chunk_index=draft.chunk_index,
                    body=body,
                    section_title=draft.section_title,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    active=document.active,
                    embedding=vector,
                )
            )
        document.status = "ready"
        document.error_message = None
        document.document_type = document_type
        document.document_date = document_date
