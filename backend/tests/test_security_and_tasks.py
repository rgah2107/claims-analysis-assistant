"""Upload checks, log redaction, and the Celery retry policy."""

from __future__ import annotations

import io
import zipfile

import pytest
from app.errors import PermanentIngestError, TransientIngestError, UploadRejected
from app.security import redact, validate_claim_number, validate_upload
from app.tasks import decide_retry, process_document
from celery.exceptions import Retry


def test_kombu_accepts_the_pinned_redis_client() -> None:
    """Compose connects for real. The in-memory broker never executes this check."""
    from kombu.transport.redis import Channel

    class _Bare:
        global_keyprefix = None

    assert Channel._get_client(_Bare()) is not None


def test_rejects_empty_files_wrong_types_and_disguised_zips() -> None:
    """Magic bytes are checked so a renamed file is not trusted."""
    with pytest.raises(UploadRejected):
        validate_upload("vacio.pdf", b"", 1000)
    with pytest.raises(UploadRejected):
        validate_upload("notas.txt", b"hola", 1000)
    with pytest.raises(UploadRejected):
        validate_upload("falso.pdf", b"not a pdf", 1000)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "no es word")
    with pytest.raises(UploadRejected):
        validate_upload("carta.docx", buffer.getvalue(), 1000)
    assert validate_upload("real.pdf", b"%PDF-1.4\n", 1000) == "real.pdf"


def test_claim_numbers_cannot_escape_the_storage_directory() -> None:
    """The claim number becomes a directory name, so slashes are rejected."""
    assert validate_claim_number("SIN-1001") == "SIN-1001"
    with pytest.raises(UploadRejected):
        validate_claim_number("../otro")


def test_redaction_masks_contact_details_and_ids() -> None:
    """Logs may quote an exception. They must not keep these identifiers."""
    raw = "avisar a ana.ruiz@norte.test o al 612 345 678, dni 12345678Z, nie X1234567L"
    masked = redact(raw)
    assert "ana.ruiz@norte.test" not in masked
    assert "612" not in masked
    assert "12345678Z" not in masked
    assert "X1234567L" not in masked
    assert "[email]" in masked
    assert "[dni]" in masked
    assert "[nie]" in masked


def test_backoff_is_exponential_and_then_stops() -> None:
    """2s, 4s, 8s, then the document is marked failed."""
    error = TransientIngestError("database restarting")
    assert decide_retry(error, 0, 3).countdown == 2
    assert decide_retry(error, 1, 3).countdown == 4
    assert decide_retry(error, 2, 3).countdown == 8
    final = decide_retry(error, 3, 3)
    assert final.action == "fail"
    assert decide_retry(PermanentIngestError("PDF corrupto"), 0, 3).action == "fail"


def test_task_survives_a_dead_worker_and_retries_transient_errors(monkeypatch) -> None:
    """acks_late is the dead-worker setting. The first transient error waits 2 seconds."""
    assert process_document.acks_late is True
    assert process_document.reject_on_worker_lost is True
    assert process_document.max_retries == 3

    calls = {"n": 0}
    failed: dict[str, str] = {}

    def explode(_document_id: str) -> None:
        calls["n"] += 1
        raise TransientIngestError("redis blip")

    monkeypatch.setattr("app.tasks.run_ingest", explode)
    monkeypatch.setattr(
        "app.tasks.mark_failed",
        lambda document_id, message: failed.update(document_id=document_id, message=message),
    )
    # Eager apply raises Retry instead of sleeping. The countdown is the backoff.
    with pytest.raises(Retry) as raised:
        process_document.apply(args=["doc-1"])
    assert calls["n"] == 1
    assert raised.value.when == 2
    assert failed == {}


def test_permanent_errors_are_not_retried(monkeypatch) -> None:
    """A corrupt file fails once. Retrying it would fail the same way."""
    calls = {"n": 0}

    def explode(_document_id: str) -> None:
        calls["n"] += 1
        raise PermanentIngestError("PDF corrupto")

    seen: list[str] = []
    monkeypatch.setattr("app.tasks.run_ingest", explode)
    monkeypatch.setattr("app.tasks.mark_failed", lambda _document_id, message: seen.append(message))
    process_document.apply(args=["doc-2"])
    assert calls["n"] == 1
    assert seen == ["PDF corrupto"]
