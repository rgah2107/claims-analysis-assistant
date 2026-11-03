"""Database engine and short-lived sessions.

The engine is created on first use. If ``DATABASE_URL`` changes, the next call
replaces the engine. That is what lets the Docker integration test start
Postgres and then point the app at it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_engine_url: str | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide engine, recreating it when the URL changes."""
    global _engine, _engine_url, _session_factory
    url = get_settings().database_url
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(url, pool_pre_ping=True)
        _engine_url = url
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine. Tests call this after exporting a new URL."""
    global _engine, _engine_url, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
    _session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success and roll back on any exception.

    Callers that stream a response should not hold this open across a model
    call. Open a session, read or write, and close it before waiting on the network.
    """
    # get_engine() first so a URL change replaces the cached factory before we use it.
    get_engine()
    factory = _session_factory
    if factory is None:
        raise RuntimeError("database engine is not initialised")
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# One id for the whole product. The API and every Celery child take this lock
# before DDL. The number itself is arbitrary; it only has to stay stable.
_SCHEMA_LOCK = 824651239

_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops)
    """,
    """
    CREATE INDEX IF NOT EXISTS chunks_search_gin
    ON chunks USING gin (search_vector)
    """,
    # One active file per name, and one active copy of the same bytes.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS documents_one_active_name
    ON documents (claim_id, filename)
    WHERE active
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS documents_one_active_hash
    ON documents (claim_id, sha256)
    WHERE active
    """,
)


def init_db() -> None:
    """Create the vector extension, the tables, and the search indexes.

    ``create_all`` is enough for this MVP. A second iteration would move this
    into Alembic revisions. The HNSW and GIN indexes are raw SQL because the
    vector operator class does not belong in the ORM model declaration.

    ``CREATE EXTENSION IF NOT EXISTS`` still raises a unique violation when two
    sessions pass the existence check together. Postgres logs that as an ERROR.
    The transaction lock makes the API and the worker processes wait their turn,
    then the second caller sees the objects and does nothing.
    """
    from app.models import Base

    engine = get_engine()
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK})
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(connection)
        for statement in _INDEXES:
            connection.execute(text(statement))
