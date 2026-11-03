"""Accept files and enqueue ingest.

The HTTP call stores bytes, commits the rows, and only then enqueues. The
response is built after the enqueue attempt, so a broker failure is already
visible as a failed document. In demo tests Celery runs eagerly, and the
response then already says ``ready`` because the work finished inline.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Chunk, Claim, Document, Job
from app.observability import get_logger, start_span
from app.pipeline import mark_failed
from app.schemas import DocumentOut, UploadResponse
from app.security import validate_claim_number, validate_upload
from app.tasks import process_document

logger = get_logger()


def accept_documents(claim_number: str, files: list[tuple[str, bytes]]) -> UploadResponse:
    """Validate a batch, store it, and enqueue one task per new file."""
    if not files:
        from app.errors import UploadRejected

        raise UploadRejected("No file was received.")
    claim_number = validate_claim_number(claim_number)
    settings = get_settings()
    checked = [(validate_upload(name, data, settings.max_upload_bytes), data) for name, data in files]

    with start_span("upload", **{"file.count": len(checked)}):
        with session_scope() as session:
            claim = session.scalars(
                select(Claim).where(Claim.claim_number == claim_number)
            ).first()
            if claim is None:
                claim = Claim(claim_number=claim_number)
                session.add(claim)
                session.flush()
            job = Job(claim_id=claim.id, document_ids=[])
            session.add(job)
            session.flush()
            document_ids: list[str] = []
            to_enqueue: list[str] = []
            for filename, data in checked:
                document_id, should_enqueue = _store_one(
                    session, claim, job.id, claim_number, filename, data
                )
                document_ids.append(document_id)
                if should_enqueue:
                    to_enqueue.append(document_id)
            job.document_ids = document_ids
            job_id = job.id

    for document_id in to_enqueue:
        try:
            process_document.delay(document_id)
        except Exception as exc:
            # The bytes are already stored. The adjuster sees the enqueue error
            # on the document instead of a failed HTTP call with an orphaned file.
            logger.info("enqueue failed for %s", document_id)
            mark_failed(document_id, f"Could not enqueue processing: {exc}")

    with session_scope() as session:
        rows = session.scalars(select(Document).where(Document.id.in_(document_ids))).all()
        by_id = {row.id: row for row in rows}
        ordered = [by_id[document_id] for document_id in document_ids if document_id in by_id]
        return UploadResponse(job_id=job_id, documents=[to_document_out(row) for row in ordered])


def list_documents(claim_number: str) -> list[DocumentOut]:
    """Every version for the claim, newest first. Inactive rows are previous versions."""
    with session_scope() as session:
        claim = session.scalars(select(Claim).where(Claim.claim_number == claim_number)).first()
        if claim is None:
            return []
        rows = session.scalars(
            select(Document)
            .where(Document.claim_id == claim.id)
            .order_by(Document.created_at.desc())
        ).all()
        return [to_document_out(row) for row in rows]


def get_job(job_id: str) -> tuple[str, list[DocumentOut]] | None:
    """Return the claim number and the documents named on this upload receipt."""
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        claim = session.get(Claim, job.claim_id)
        if claim is None:
            return None
        rows = session.scalars(select(Document).where(Document.id.in_(job.document_ids or []))).all()
        by_id = {row.id: row for row in rows}
        ordered = [by_id[document_id] for document_id in job.document_ids if document_id in by_id]
        return claim.claim_number, [to_document_out(row) for row in ordered]


def to_document_out(document: Document) -> DocumentOut:
    """Map a row to the public document shape."""
    return DocumentOut(
        id=document.id,
        filename=document.filename,
        version=document.version,
        status=document.status,
        error_message=document.error_message,
        active=document.active,
        document_type=document.document_type,
        document_date=document.document_date,
        size_bytes=document.size_bytes,
    )


def _store_one(session, claim: Claim, job_id: str, claim_number: str, filename: str, data: bytes):
    """Insert a new version, or reuse the active row when the bytes are unchanged.

    The bool tells the caller whether a worker task is required.
    """
    digest = hashlib.sha256(data).hexdigest()
    existing = session.scalars(
        select(Document).where(
            Document.claim_id == claim.id,
            Document.sha256 == digest,
            Document.active.is_(True),
        )
    ).first()
    if existing is not None:
        return existing.id, False

    previous = session.scalars(
        select(Document).where(
            Document.claim_id == claim.id,
            Document.filename == filename,
            Document.active.is_(True),
        )
    ).all()
    version = 1
    if previous:
        version = max(document.version for document in previous) + 1
        for document in previous:
            document.active = False
            session.query(Chunk).filter(Chunk.document_id == document.id).update(
                {"active": False},
                synchronize_session=False,
            )

    content_type = (
        "application/pdf" if filename.lower().endswith(".pdf") else
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    document = Document(
        claim_id=claim.id,
        job_id=job_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        version=version,
        status="queued",
        storage_path="",
        active=True,
    )
    session.add(document)
    session.flush()
    document.storage_path = _write_bytes(claim_number, document.id, filename, data)
    return document.id, True


def _write_bytes(claim_number: str, document_id: str, filename: str, data: bytes) -> str:
    """Write the original file where the worker will read it."""
    path = Path(get_settings().data_dir) / claim_number / document_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)
