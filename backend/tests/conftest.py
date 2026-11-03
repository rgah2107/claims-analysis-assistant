"""Test defaults.

Environment variables are set before the application is imported so the fake
providers and the in-memory Celery broker are the ones the process builds.
Integration tests replace DATABASE_URL with the container they start.
"""

from __future__ import annotations

import os

os.environ["OTEL_EXPORTER"] = "none"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["LLM_FALLBACK"] = ""
os.environ["EMBEDDING_PROVIDER"] = "fake"
os.environ["REDIS_URL"] = "memory://"
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://claims:claims@localhost:5432/claims",
)

from app.tasks import celery  # noqa: E402

# Eager mode runs the task inside delay(), so upload tests do not need Redis.
celery.conf.task_always_eager = True
celery.conf.task_eager_propagates = True
