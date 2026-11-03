"""Celery entrypoint for ingest.

``acks_late`` together with ``reject_on_worker_lost`` is the dead-worker policy.
The broker keeps the message until the task returns. If the worker process is
killed mid-task, the message is redelivered to another worker. ``run_ingest``
is safe under that redelivery: chunks are written once, at the end, in one
transaction, and a document already marked ready is a no-op.

Retries use exponential backoff: 2s, 4s, then 8s. The fourth failure is stored
on the document and is not retried again. Corrupt files do not use that budget.
"""

from __future__ import annotations

from dataclasses import dataclass

from celery import Celery
from celery.signals import worker_process_init

from app.config import get_settings
from app.errors import PermanentIngestError
from app.pipeline import mark_failed, run_ingest

settings = get_settings()
celery = Celery("claims", broker=settings.redis_url)
celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)


@dataclass(frozen=True)
class RetryDecision:
    """What the task should do with one exception."""

    action: str
    countdown: int | None
    message: str


def decide_retry(exc: Exception, retries: int, max_retries: int) -> RetryDecision:
    """Map an exception to fail or to retry with backoff ``2 ** (retries + 1)`` seconds.

    ``retries`` is how many retries have already been scheduled, which is what
    Celery stores on ``self.request.retries`` at the start of an attempt.
    """
    if isinstance(exc, PermanentIngestError):
        return RetryDecision("fail", None, str(exc))
    if retries >= max_retries:
        return RetryDecision("fail", None, f"Retries exhausted: {exc}")
    return RetryDecision("retry", 2 ** (retries + 1), str(exc))


@celery.task(
    bind=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
    name="claims.process_document",
)
def process_document(self, document_id: str) -> None:
    """Ingest one document id. The broker, not the HTTP request, runs this."""
    try:
        run_ingest(document_id)
    except PermanentIngestError as exc:
        mark_failed(document_id, str(exc))
        return
    except Exception as exc:
        decision = decide_retry(exc, self.request.retries, self.max_retries)
        if decision.action == "fail":
            mark_failed(document_id, decision.message)
            return
        raise self.retry(exc=exc, countdown=decision.countdown)


@worker_process_init.connect
def boot_worker(**_kwargs) -> None:
    """Prepare logging, traces, and tables inside each worker process."""
    from app.db import init_db
    from app.observability import setup_logging, setup_telemetry

    setup_logging()
    setup_telemetry("claims-worker")
    init_db()
