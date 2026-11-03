"""HTTP bodies and the structured claim summary.

``ClaimSummary`` is the contract the model must satisfy. ``extra="forbid"``
makes a surprising field a validation failure, which is what triggers the
single repair attempt.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contradiction(BaseModel):
    """Two or more sources disagree on one topic. Both stay visible."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    statements: list[str] = Field(min_length=2)
    citation_chunk_ids: list[str] = Field(default_factory=list)


class ClaimSummary(BaseModel):
    """Fields the brief requires, plus contradictions when sources disagree.

    Nulls are allowed. An invented policy number is not: if the evidence does
    not contain a field, the field stays null and confidence stays low.
    """

    model_config = ConfigDict(extra="forbid")

    policy_number: str | None = None
    dates: list[str] = Field(default_factory=list)
    applicable_coverage: str | None = None
    confidence: Literal["low", "medium", "high"]
    contradictions: list[Contradiction] = Field(default_factory=list)


class DocumentOut(BaseModel):
    """One row in the document list the adjuster polls."""

    id: str
    filename: str
    version: int
    status: str
    error_message: str | None
    active: bool
    document_type: str
    document_date: str | None
    size_bytes: int


class UploadResponse(BaseModel):
    """Returned immediately. Processing has not finished yet."""

    job_id: str
    documents: list[DocumentOut]


class JobOut(BaseModel):
    """A job is the group of files accepted by one upload call."""

    job_id: str
    claim_number: str
    documents: list[DocumentOut]


class QuestionIn(BaseModel):
    """The natural-language question for one claim."""

    question: str = Field(min_length=3, max_length=2000)


class FeedbackIn(BaseModel):
    """Useful / not useful, with an optional short note."""

    useful: bool
    comment: str | None = Field(default=None, max_length=1000)


class HealthOut(BaseModel):
    """Tells the UI whether this process is in demo mode."""

    status: str
    llm_provider: str
    embedding_provider: str
    database: str
