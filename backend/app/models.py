"""Tables for claims, ingest jobs, documents, chunks, answers, and feedback.

``claim_id`` is copied onto ``chunks`` on purpose. Retrieval always filters that
column, so a forgotten join cannot search another claim's vectors.

``search_vector`` is a generated column over the Spanish text-search
configuration. Accent folding is not applied here: the stock ``unaccent``
function is STABLE, and PostgreSQL refuses it inside a generated column.
Adding an IMMUTABLE wrapper is a measured second step, not a default.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Computed, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Fixed to the fake embedder and to the Gemini request. Changing it means a new index.
EMBEDDING_DIMENSION = 384


def utcnow() -> datetime:
    """Timezone-aware timestamp for columns that record when a row changed."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """String UUID. Stored as text so the API can return it without conversion."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Shared declarative base. ``init_db`` creates every subclass."""


class Claim(Base):
    """One insurance claim. Documents and answers always belong to exactly one."""

    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    documents: Mapped[list[Document]] = relationship(back_populates="claim")


class Job(Base):
    """The id returned by upload, before any worker has started."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), nullable=False)
    # Receipt of this upload, including a file that was already ingested.
    document_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    documents: Mapped[list[Document]] = relationship(back_populates="job")


class Document(Base):
    """One stored file and the progress of its ingest.

    A new upload of the same name deactivates the previous row. The old row
    stays so a citation from an answer that was already shown can still be opened.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(180), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # queued, extracting, chunking, embedding, indexing, ready, failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    document_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    claim: Mapped[Claim] = relationship(back_populates="documents")
    job: Mapped[Job] = relationship(back_populates="documents")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document")


class Chunk(Base):
    """One retrievable fragment of one document version."""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    # Generated from ``body``. The application never writes this column.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR(),
        Computed("to_tsvector('english', body)", persisted=True),
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class Answer(Base):
    """A stored question, the text that was streamed, and the validated summary."""

    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # pending, answered, insufficient_evidence, schema_error, provider_error
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    feedback: Mapped[list[Feedback]] = relationship(back_populates="answer")


class Feedback(Base):
    """The adjuster's useful / not useful vote on one answer."""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    answer_id: Mapped[str] = mapped_column(ForeignKey("answers.id"), nullable=False, index=True)
    useful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    answer: Mapped[Answer] = relationship(back_populates="feedback")
