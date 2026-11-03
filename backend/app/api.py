"""HTTP routes.

Upload returns 202 as soon as the files are stored. The question route returns
``text/event-stream`` and does not buffer the body. Feedback is a separate call
so a vote does not require the stream to still be open.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text

from app.answering import list_answers, save_feedback, stream_answer
from app.config import get_settings
from app.db import session_scope
from app.errors import UploadRejected
from app.schemas import FeedbackIn, HealthOut, JobOut, QuestionIn, UploadResponse
from app.uploads import accept_documents, get_job, list_documents

router = APIRouter()


@router.get("/api/health", response_model=HealthOut)
def health() -> JSONResponse:
    """Report demo-mode providers and whether Postgres answers."""
    settings = get_settings()
    database = "ok"
    status = "ok"
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        database = "down"
        status = "degraded"
    payload = HealthOut(
        status=status,
        llm_provider=settings.llm_provider,
        embedding_provider=settings.embedding_provider,
        database=database,
    )
    return JSONResponse(status_code=200 if status == "ok" else 503, content=payload.model_dump())


@router.post("/api/claims/{claim_number}/documents", status_code=202, response_model=UploadResponse)
async def upload_documents(claim_number: str, files: list[UploadFile] = File(...)) -> UploadResponse:
    """Store one or more PDF/DOCX files and return a job id without waiting."""
    payload = [(upload.filename or "", await upload.read()) for upload in files]
    try:
        return accept_documents(claim_number, payload)
    except UploadRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/claims/{claim_number}/documents")
def documents(claim_number: str) -> dict:
    """Document list for the polling panel, including previous versions."""
    return {"documents": [item.model_dump() for item in list_documents(claim_number)]}


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def job(job_id: str) -> JobOut:
    """The receipt returned by upload."""
    found = get_job(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail="That job does not exist.")
    claim_number, documents_out = found
    return JobOut(job_id=job_id, claim_number=claim_number, documents=documents_out)


@router.post("/api/claims/{claim_number}/questions")
def ask(claim_number: str, body: QuestionIn) -> StreamingResponse:
    """Stream citations, tokens, and the validated summary for one question."""
    generator = stream_answer(claim_number, body.question)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/claims/{claim_number}/answers")
def answers(claim_number: str) -> dict:
    """Previous answers, so a refresh does not wipe the conversation."""
    return {"answers": list_answers(claim_number)}


@router.post("/api/answers/{answer_id}/feedback", status_code=204)
def feedback(answer_id: str, body: FeedbackIn) -> Response:
    """Record useful / not useful. 204 has no body. 404 when the answer does not exist."""
    if not save_feedback(answer_id, body.useful, body.comment):
        raise HTTPException(status_code=404, detail="That answer does not exist.")
    return Response(status_code=204)
