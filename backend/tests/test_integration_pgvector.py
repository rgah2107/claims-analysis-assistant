"""Real PostgreSQL + pgvector, started by this test through Docker.

This is the integration test the brief asks for. The test process itself starts
``pgvector/pgvector:pg16`` with testcontainers. It does not assume a database is
already running. Set DATABASE_URL_TEST to point at an existing database, for
example the Compose one, and the container is not started.

The checks are the ones that only a real database can prove: hybrid search does
not cross claims, full-text and vectors both run, uploading the same bytes does
not duplicate chunks, and a new version leaves the old citation rows in place.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.db import get_engine, init_db, reset_engine, session_scope
from app.models import Base, Chunk
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.pdf_bytes import build_pdf

pytestmark = pytest.mark.integration

POLICY = (
    "Policy number: POL-NS-2024-1187.\n"
    "Insured: Ana Ruiz.\n"
    "Term: 01/03/2024 to 01/03/2025.\n"
    "CLAUSE 4. Water damage\n"
    "The indemnity limit for water damage is 2000 EUR per claim.\n"
    "The deductible for water damage is 150 EUR."
)
EXCLUSION = (
    "CLAUSE 7. Exclusions\n"
    "Damage caused by lack of maintenance is not covered.\n"
    "CLAUSE 12. Notice period\n"
    "The insured must report the claim within 7 days."
)


def test_hybrid_search_is_limited_to_the_claim_and_cites_the_page(client) -> None:
    """Claim B has the same wording. A question about claim A must not cite it."""
    policy = build_pdf([POLICY, EXCLUSION])
    upload(client, "SIN-A", "home-policy.pdf", policy, "application/pdf")
    upload(client, "SIN-B", "home-policy.pdf", policy, "application/pdf")

    events = ask(client, "SIN-A", "What is the policy number")
    done = _event(events, "done")
    assert done["outcome"] == "answered"
    citations = [payload for name, payload in events if name == "citation"]
    assert citations
    assert all(citation["filename"] == "home-policy.pdf" for citation in citations)
    assert any("POL-NS-2024-1187" in citation["quote"] for citation in citations)
    assert _event(events, "summary")["policy_number"] == "POL-NS-2024-1187"
    answer_text = "".join(payload["text"] for name, payload in events if name == "token")
    assert "POL-NS-2024-1187" in answer_text
    assert "POL-OTHER" not in answer_text

    # The other claim is a different row even though the bytes match.
    with session_scope() as session:
        claim_ids = set(session.scalars(select(Chunk.claim_id)).all())
    assert len(claim_ids) == 2


def test_same_bytes_are_idempotent_and_a_new_version_keeps_old_chunks(client) -> None:
    """A retry of the same file does not double the index. A changed file does not delete history."""
    original = build_pdf([POLICY, EXCLUSION])
    first = upload(client, "SIN-VERS", "home-policy.pdf", original, "application/pdf")
    assert first[0]["status"] == "ready"
    first_count = _chunk_count()

    second = upload(client, "SIN-VERS", "home-policy.pdf", original, "application/pdf")
    assert second[0]["id"] == first[0]["id"]
    assert _chunk_count() == first_count

    changed = build_pdf([POLICY + "\nAdditional premium payment condition.", EXCLUSION])
    third = upload(client, "SIN-VERS", "home-policy.pdf", changed, "application/pdf")
    assert third[0]["version"] == 2
    assert third[0]["active"] is True
    with session_scope() as session:
        rows = session.scalars(select(Chunk)).all()
        active = [row for row in rows if row.active]
        inactive = [row for row in rows if not row.active]
    assert active
    assert inactive
    assert any("POL-NS-2024-1187" in row.body for row in inactive)
    assert any("premium" in row.body for row in active)


def test_letter_contradicts_the_policy_and_an_unknown_question_is_refused(client) -> None:
    """The endorsement changes the limit. A question about jewelry has no evidence."""
    upload(client, "SIN-MIX", "home-policy.pdf", build_pdf([POLICY, EXCLUSION]), "application/pdf")
    upload(client, "SIN-MIX", "endorsement-clause-4.docx", _letter_bytes(), _docx_type())

    conflict = ask(client, "SIN-MIX", "What is the indemnity limit for water damage")
    assert _event(conflict, "done")["outcome"] == "answered"
    text = "".join(payload["text"] for name, payload in conflict if name == "token")
    summary = _event(conflict, "summary")
    assert "2000" in text and "5000" in text
    assert summary["contradictions"]
    filenames = {payload["filename"] for name, payload in conflict if name == "citation"}
    assert filenames == {"home-policy.pdf", "endorsement-clause-4.docx"}

    refused = ask(client, "SIN-MIX", "Does it cover jewelry theft at the airport")
    assert _event(refused, "done")["outcome"] == "insufficient_evidence"
    assert any(name == "insufficient_evidence" for name, _payload in refused)


def test_corrupt_pdf_fails_the_document_and_feedback_is_stored(client) -> None:
    """The batch is not stuck: the bad file is failed and a good answer can be voted on."""
    broken = upload(client, "SIN-BAD", "broken.pdf", b"%PDF-1.4\nthis is not a valid pdf", "application/pdf")
    assert broken[0]["status"] == "failed"
    assert broken[0]["error_message"]

    upload(client, "SIN-BAD", "home-policy.pdf", build_pdf([POLICY]), "application/pdf")
    events = ask(client, "SIN-BAD", "What is the policy number")
    answer_id = _event(events, "done")["answer_id"]
    vote = client.post(
        f"/api/answers/{answer_id}/feedback",
        json={"useful": True, "comment": "correct citation"},
    )
    assert vote.status_code == 204


@pytest.fixture(scope="session")
def pg_url():
    """Start pgvector unless the caller already pointed us at a database."""
    external = os.environ.get("DATABASE_URL_TEST")
    if external:
        os.environ["DATABASE_URL"] = external
        yield external
        return
    if not _docker_ready():
        if os.environ.get("CI"):
            pytest.fail("CI must provide Docker so the pgvector test can start a database.")
        pytest.skip("Docker is not running. This test starts pgvector itself.")
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        "pgvector/pgvector:pg16",
        username="claims",
        password="claims",
        dbname="claims",
        driver="psycopg",
    )
    container.start()
    url = container.get_connection_url()
    os.environ["DATABASE_URL"] = url
    yield url
    container.stop()


@pytest.fixture
def client(pg_url, tmp_path, monkeypatch):
    """An API client bound to the container and a throwaway upload directory."""
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OTEL_EXPORTER", "none")
    reset_engine()
    init_db()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()
    with session_scope() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())


def test_parallel_schema_creation_does_not_raise(pg_url, monkeypatch) -> None:
    """The API and several worker processes call this together at Compose startup."""
    monkeypatch.setenv("DATABASE_URL", pg_url)
    reset_engine()
    get_engine()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _index: init_db(), range(4)))


def upload(client: TestClient, claim: str, filename: str, payload: bytes, content_type: str) -> list[dict]:
    """Post one file and return the document objects from the 202 response."""
    response = client.post(
        f"/api/claims/{claim}/documents",
        files=[("files", (filename, io.BytesIO(payload), content_type))],
    )
    assert response.status_code == 202, response.text
    return response.json()["documents"]


def ask(client: TestClient, claim: str, question: str) -> list[tuple[str, dict]]:
    """Read the whole SSE body. TestClient waits until the generator finishes."""
    response = client.post(f"/api/claims/{claim}/questions", json={"question": question})
    assert response.status_code == 200, response.text
    return _parse_sse(response.text)


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Split SSE frames. JSON is split on the first colon only."""
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        event_name = "message"
        data = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if data:
            events.append((event_name, json.loads(data)))
    return events


def _event(events: list[tuple[str, dict]], name: str) -> dict:
    """The last payload of one event type."""
    matches = [payload for event_name, payload in events if event_name == name]
    assert matches, name
    return matches[-1]


def _chunk_count() -> int:
    """How many chunk rows exist, active or not."""
    with session_scope() as session:
        return len(session.scalars(select(Chunk.id)).all())


def _letter_bytes() -> bytes:
    """The endorsement that changes the water-damage limit."""
    document = Document()
    document.add_paragraph("Endorsement. Date: 15/06/2024.")
    document.add_paragraph("Policy POL-NS-2024-1187.")
    document.add_paragraph(
        "Clause 4 is amended. The indemnity limit for water damage "
        "is changed to 5000 EUR per claim."
    )
    document.add_paragraph("The deductible does not change.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _docx_type() -> str:
    """The content type sent with the endorsement."""
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docker_ready() -> bool:
    """True when the docker CLI can talk to a daemon."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0
