"""Structured logs and traces.

Spans carry ids, timings, token estimates, and an estimated cost. They do not
carry document text, prompt bodies, or the question. Those stay in Postgres.
The log filter adds the current trace id and redacts anything that still looks
like an email, a phone, or a national id.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Span

from app.config import get_settings
from app.security import redact

LOGGER_NAME = "claims"


def estimate_tokens(text: str) -> int:
    """Rough token count for a trace attribute. This is not the provider tokenizer."""
    return max(1, len(text) // 4)


def estimate_cost_usd(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Turn the token estimate into a dollar estimate from the configured price table."""
    settings = get_settings()
    prompt_price = settings.price_per_million_prompt.get(provider, 0.0)
    completion_price = settings.price_per_million_completion.get(provider, 0.0)
    return (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, with a trace id when a span is active."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class _TraceAndRedactFilter(logging.Filter):
    """Attach the active trace id and mask personal data in the message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(redact(str(item)) for item in record.args)
        span = trace.get_current_span()
        context = span.get_span_context()
        record.trace_id = format(context.trace_id, "032x") if context.is_valid else "-"
        return True


def setup_logging() -> None:
    """Install one JSON handler on the application logger."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_TraceAndRedactFilter())
    logger.addHandler(handler)
    logger.propagate = False


def get_logger() -> logging.Logger:
    """Logger used by the API and the worker."""
    return logging.getLogger(LOGGER_NAME)


# Fields a reviewer can use. The rest of an HTTP span is the SDK's peer address
# and user agent, which turn one request into a screen of JSON.
_CONSOLE_FIELDS = (
    "http.method",
    "http.route",
    "http.status_code",
    "claim.id",
    "document.id",
    "file.count",
    "chunk.count",
    "retrieved_chunk_count",
    "evidence.sufficient",
    "llm.provider",
    "llm.prompt_tokens_estimate",
    "llm.completion_tokens_estimate",
    "llm.estimated_cost_usd",
    "summary.status",
)


def console_line(span: ReadableSpan) -> str | None:
    """One log line for a span, or None when the span is an ASGI body chunk.

    FastAPI's instrumentation opens a span for every streamed chunk. Printing
    those in full is why one answer fills the Compose log. The request span and
    the spans this application opens are the ones worth reading.
    """
    attributes = dict(span.attributes or {})
    if "asgi.event.type" in attributes:
        return None
    duration_ms = max(0, (span.end_time - span.start_time) / 1_000_000) if span.end_time else 0
    parts = [f"trace {span.name} {duration_ms:.0f}ms"]
    for key in _CONSOLE_FIELDS:
        if key in attributes:
            parts.append(f"{key}={attributes[key]}")
    return " ".join(parts)


class _OneLineSpanExporter(SpanExporter):
    """Write :func:`console_line` to stdout. OTLP export is unchanged."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            line = console_line(span)
            if line is not None:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        return SpanExportResult.SUCCESS


def setup_telemetry(service_name: str) -> None:
    """Install a tracer provider. Tests set ``OTEL_EXPORTER=none`` and export nothing."""
    settings = get_settings()
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if settings.otel_exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(_OneLineSpanExporter()))
    elif settings.otel_exporter == "otlp":
        # Imported here so a missing exporter dependency fails only when selected.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_endpoint}/v1/traces"))
        )
    trace.set_tracer_provider(provider)


@contextmanager
def start_span(name: str, **attributes: str | int | float | bool) -> Iterator[Span]:
    """Open a span and set attributes that are safe to export.

    Callers must not pass document text, prompts, or questions as attributes.
    """
    tracer = trace.get_tracer("claims")
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
        yield span


def record_llm_usage(span: Span, provider_name: str, prompt: str, completion: str) -> None:
    """Record sizes and estimated cost. The texts themselves are not attributes."""
    prompt_tokens = estimate_tokens(prompt)
    completion_tokens = estimate_tokens(completion)
    span.set_attribute("llm.provider", provider_name)
    span.set_attribute("llm.prompt_tokens_estimate", prompt_tokens)
    span.set_attribute("llm.completion_tokens_estimate", completion_tokens)
    span.set_attribute(
        "llm.estimated_cost_usd",
        estimate_cost_usd(provider_name, prompt_tokens, completion_tokens),
    )
