"""Stream an answer for one claim.

Order of events, on purpose:

1. ``start`` with the answer id, so feedback can be attached even if the client
   keeps the stream open.
2. ``retrieval`` and one ``citation`` per hit, before any generated sentence.
   The adjuster sees the evidence while the model is still producing text.
3. ``token`` events for the prose.
4. ``summary`` after Pydantic accepts it, or ``schema_error`` when it does not.
5. ``done`` with the final outcome.

A weak retrieval emits ``insufficient_evidence`` and does not call the model.
The summary in that case is built from the hits with coverage forced to null.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.embeddings import get_embedding_provider
from app.errors import ProviderError
from app.grounding import blocks_from_hits, summary_payload
from app.models import Answer, Claim, Feedback
from app.observability import get_logger, record_llm_usage, start_span
from app.prompts import build_answer_prompt
from app.providers import build_llm_router
from app.retrieval import RetrievedChunk, evidence_is_sufficient, hybrid_search
from app.schemas import ClaimSummary
from app.summary import empty_summary, summarize

logger = get_logger()

REFUSAL = (
    "I cannot answer from the documents on this claim. "
    "The retrieved evidence is not enough."
)
SCHEMA_MESSAGE = "An answer was produced, but the structured summary failed validation."
PROVIDER_MESSAGE = "The language provider is unavailable."


def stream_answer(claim_number: str, question: str) -> Iterator[str]:
    """Yield Server-Sent Events for one question. Persist the answer as we go."""
    with session_scope() as session:
        claim = session.scalars(select(Claim).where(Claim.claim_number == claim_number)).first()
        if claim is None:
            yield _sse("error", {"message": "That claim does not exist. Upload documents first."})
            return
        answer = Answer(claim_id=claim.id, question=question.strip(), answer_text="", outcome="pending")
        session.add(answer)
        session.flush()
        claim_id = claim.id
        answer_id = answer.id

    yield _sse("start", {"answer_id": answer_id})
    settings = get_settings()
    started = time.perf_counter()
    try:
        with start_span("retrieval", **{"claim.id": claim_id}) as span:
            vector = get_embedding_provider().embed([question])[0]
            with session_scope() as session:
                hits = hybrid_search(
                    session,
                    claim_id,
                    question,
                    vector,
                    candidates=settings.retrieval_candidates,
                    limit=settings.retrieval_limit,
                    rrf_k=settings.rrf_k,
                )
            span.set_attribute("retrieved_chunk_count", len(hits))
            sufficient = evidence_is_sufficient(question, hits)
            span.set_attribute("evidence.sufficient", sufficient)
    except ProviderError:
        logger.info("embedding provider failed for answer %s", answer_id)
        _save(answer_id, outcome="provider_error", text="", hits=[], summary=None)
        yield _sse("error", {"message": PROVIDER_MESSAGE, "answer_id": answer_id})
        yield _sse("done", {"answer_id": answer_id, "outcome": "provider_error"})
        return

    latency_ms = int((time.perf_counter() - started) * 1000)
    yield _sse(
        "retrieval",
        {"chunk_count": len(hits), "sufficient": sufficient, "latency_ms": latency_ms},
    )
    for hit in hits:
        yield _sse("citation", _citation(hit))

    if not sufficient:
        summary = empty_summary(question, hits)
        _save(
            answer_id,
            outcome="insufficient_evidence",
            text=REFUSAL,
            hits=hits,
            summary=summary,
        )
        yield _sse("summary", summary.model_dump())
        yield _sse("insufficient_evidence", {"message": REFUSAL, "answer_id": answer_id})
        yield _sse("done", {"answer_id": answer_id, "outcome": "insufficient_evidence"})
        return

    prompt = build_answer_prompt(question, hits)
    pieces: list[str] = []
    try:
        router = build_llm_router()
        with start_span("llm.answer", **{"claim.id": claim_id}) as span:
            for piece in router.stream(prompt):
                pieces.append(piece)
                yield _sse("token", {"text": piece})
            record_llm_usage(span, router.last_provider, prompt, "".join(pieces))
    except ProviderError as exc:
        logger.info("llm provider failed for answer %s: %s", answer_id, exc)
        _save(answer_id, outcome="provider_error", text="".join(pieces), hits=hits, summary=None)
        yield _sse("error", {"message": PROVIDER_MESSAGE, "answer_id": answer_id})
        yield _sse("done", {"answer_id": answer_id, "outcome": "provider_error"})
        return

    answer_text = "".join(pieces)
    with start_span("llm.summary", **{"claim.id": claim_id}) as span:
        summary, status = summarize(router, question, hits)
        record_llm_usage(span, router.last_provider, prompt, answer_text)
        span.set_attribute("summary.status", status)
    if summary is None and status == "provider_error":
        # The prose already streamed. Fill the card from the same chunks
        # instead of showing a provider error for a second call that failed.
        summary = ClaimSummary.model_validate(
            summary_payload(question, blocks_from_hits(hits), abstained=False)
        )
        status = "from_documents"
    if summary is None:
        _save(answer_id, outcome="schema_error", text=answer_text, hits=hits, summary=None)
        yield _sse("schema_error", {"message": SCHEMA_MESSAGE, "answer_id": answer_id})
        yield _sse("done", {"answer_id": answer_id, "outcome": "schema_error"})
        return

    _save(answer_id, outcome="answered", text=answer_text, hits=hits, summary=summary)
    yield _sse("summary", summary.model_dump())
    yield _sse("done", {"answer_id": answer_id, "outcome": "answered", "summary_status": status})


def list_answers(claim_number: str) -> list[dict]:
    """Stored answers for a claim, oldest first, so the UI can redraw the chat."""
    with session_scope() as session:
        claim = session.scalars(select(Claim).where(Claim.claim_number == claim_number)).first()
        if claim is None:
            return []
        rows = session.scalars(
            select(Answer).where(Answer.claim_id == claim.id).order_by(Answer.created_at)
        ).all()
        return [
            {
                "answer_id": row.id,
                "question": row.question,
                "text": row.answer_text,
                "outcome": row.outcome,
                "citations": row.citations,
                "summary": row.summary,
            }
            for row in rows
        ]


def save_feedback(answer_id: str, useful: bool, comment: str | None) -> bool:
    """Store one vote. Return False when the answer id does not exist."""
    with session_scope() as session:
        answer = session.get(Answer, answer_id)
        if answer is None:
            return False
        session.add(Feedback(answer_id=answer_id, useful=useful, comment=comment))
    return True


def _save(
    answer_id: str,
    *,
    outcome: str,
    text: str,
    hits: list[RetrievedChunk],
    summary: ClaimSummary | None,
) -> None:
    """Persist the finished answer. Citations point at chunk ids, which survive a new version."""
    with session_scope() as session:
        answer = session.get(Answer, answer_id)
        if answer is None:
            return
        answer.outcome = outcome
        answer.answer_text = text
        answer.citations = [_citation(hit) for hit in hits]
        answer.summary = summary.model_dump() if summary is not None else None


def _citation(hit: RetrievedChunk) -> dict:
    """The fragment the UI shows when a citation is clicked."""
    return {
        "chunk_id": hit.chunk_id,
        "document_id": hit.document_id,
        "filename": hit.filename,
        "version": hit.version,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "section": hit.section_title,
        "quote": hit.body,
    }


def _sse(event: str, payload: dict) -> str:
    """One SSE frame. JSON keeps accents intact for the Spanish UI."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
