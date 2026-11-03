"""Retrieval and grounding numbers for the 12-question set.

Recall@5 is the fraction of answerable questions whose expected file and page
appear in the top 5 fused chunks. A page matches when it lies between
page_start and page_end.

Grounding is the fraction of answerable questions that were answered (not
refused), cite at least one expected source, and contain every expected
substring. Abstention accuracy is the fraction of unanswerable questions that
the assistant refuses.

The script uses the fake providers. It writes eval/results.md next to itself.

    python -m eval.run_eval

DATABASE_URL must point at PostgreSQL with pgvector. When it is unset and
Docker is available, the script starts pgvector/pgvector:pg16 itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")
os.environ.setdefault("OTEL_EXPORTER", "none")
os.environ.setdefault("REDIS_URL", "memory://")

from eval.build_fixtures import FIXTURES, build  # noqa: E402


def main() -> None:
    """Ingest the synthetic claim, score every question, and write the report."""
    database = _ensure_database()
    os.environ["DATABASE_URL"] = database
    from app.answering import stream_answer
    from app.config import get_settings
    from app.db import init_db, session_scope
    from app.embeddings import get_embedding_provider
    from app.retrieval import hybrid_search
    from app.tasks import celery
    from app.uploads import accept_documents

    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True
    init_db()
    if not (FIXTURES / "home-policy.pdf").exists():
        build()

    claim = _load_dataset()["claim_number"]
    policy = (FIXTURES / "home-policy.pdf").read_bytes()
    letter = (FIXTURES / "endorsement-clause-4.docx").read_bytes()
    accept_documents(claim, [("home-policy.pdf", policy), ("endorsement-clause-4.docx", letter)])

    settings = get_settings()
    rows = []
    for item in _load_dataset()["questions"]:
        with session_scope() as session:
            from app.models import Claim
            from sqlalchemy import select

            claim_row = session.scalars(select(Claim).where(Claim.claim_number == claim)).one()
            vector = get_embedding_provider().embed([item["question"]])[0]
            hits = hybrid_search(
                session,
                claim_row.id,
                item["question"],
                vector,
                candidates=settings.retrieval_candidates,
                limit=settings.retrieval_limit,
                rrf_k=settings.rrf_k,
            )
        events = _parse("".join(stream_answer(claim, item["question"])))
        done = _last(events, "done")
        text = "".join(payload.get("text", "") for name, payload in events if name == "token")
        refusal = _last(events, "insufficient_evidence")
        if refusal and not text:
            text = str(refusal.get("message", ""))
        citations = [payload for name, payload in events if name == "citation"]
        recalled = _sources_hit(hits, item["expected_sources"]) if not item["expect_abstain"] else None
        grounded = _grounded(item, done.get("outcome"), text, citations)
        rows.append(
            {
                "id": item["id"],
                "outcome": done.get("outcome"),
                "recalled": recalled,
                "grounded": grounded,
                "abstained_correctly": (
                    done.get("outcome") == "insufficient_evidence" if item["expect_abstain"] else None
                ),
            }
        )
    report = _report(rows)
    destination = Path(__file__).resolve().parent / "results.md"
    destination.write_text(report, encoding="utf-8")
    print(report)


def _ensure_database() -> str:
    """Use DATABASE_URL when it is set. Otherwise start a pgvector container."""
    configured = os.environ.get("DATABASE_URL")
    if configured:
        return configured
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        "pgvector/pgvector:pg16",
        username="claims",
        password="claims",
        dbname="claims",
        driver="psycopg",
    )
    container.start()
    # The process exits after the report, which stops the container.
    import atexit

    atexit.register(container.stop)
    return container.get_connection_url()


def _load_dataset() -> dict:
    """The committed question file."""
    path = Path(__file__).resolve().parent / "dataset.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _parse(body: str) -> list[tuple[str, dict]]:
    """The same frame split the HTTP tests use."""
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        name = "message"
        data = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if data:
            events.append((name, json.loads(data)))
    return events


def _last(events: list[tuple[str, dict]], name: str) -> dict:
    """The last payload of an event, or an empty dict when it was not emitted."""
    matches = [payload for event_name, payload in events if event_name == name]
    return matches[-1] if matches else {}


def _sources_hit(hits, expected: list[dict]) -> bool:
    """True when every expected file/page is covered by some retrieved chunk."""
    for source in expected:
        if not any(
            hit.filename == source["filename"] and hit.page_start <= source["page"] <= hit.page_end
            for hit in hits
        ):
            return False
    return True


def _grounded(item: dict, outcome: str | None, text: str, citations: list[dict]) -> bool | None:
    """None for questions that are supposed to be refused."""
    if item["expect_abstain"]:
        return None
    if outcome != "answered":
        return False
    lowered = text.lower()
    if not all(piece.lower() in lowered for piece in item["expected_substrings"]):
        return False
    return _sources_hit(_citation_hits(citations), item["expected_sources"])


def _citation_hits(citations: list[dict]):
    """Adapt SSE citations to the two attributes the source check reads."""

    class _Hit:
        def __init__(self, payload: dict) -> None:
            self.filename = payload["filename"]
            self.page_start = payload["page_start"]
            self.page_end = payload["page_end"]

    return [_Hit(payload) for payload in citations]


def _report(rows: list[dict]) -> str:
    """Markdown table plus the two headline metrics."""
    answerable = [row for row in rows if row["recalled"] is not None]
    refusals = [row for row in rows if row["abstained_correctly"] is not None]
    recall = sum(1 for row in answerable if row["recalled"]) / len(answerable)
    grounding = sum(1 for row in answerable if row["grounded"]) / len(answerable)
    abstain = sum(1 for row in refusals if row["abstained_correctly"]) / len(refusals)
    lines = [
        "# Evaluation results",
        "",
        "Fake embeddings and the extractive answerer, on `eval/dataset.json`.",
        "",
        f"- Recall@5: {recall:.2f}",
        f"- Grounding: {grounding:.2f}",
        f"- Abstention accuracy: {abstain:.2f}",
        "",
        "| Question | Outcome | Recall@5 | Grounded | Abstain ok |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['outcome']} | {_mark(row['recalled'])} | "
            f"{_mark(row['grounded'])} | {_mark(row['abstained_correctly'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _mark(value: bool | None) -> str:
    """A compact cell for the results table."""
    if value is None:
        return "—"
    return "yes" if value else "no"


if __name__ == "__main__":
    main()
