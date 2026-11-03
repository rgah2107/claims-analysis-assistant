"""Console traces stay readable. ASGI chunk spans are not printed."""

from __future__ import annotations

from app.observability import console_line


class _Span:
    def __init__(self, name: str, attributes: dict, end_time: int = 5_000_000) -> None:
        self.name = name
        self.attributes = attributes
        self.start_time = 0
        self.end_time = end_time


def test_console_line_keeps_the_fields_a_reviewer_needs() -> None:
    """One question should not dump the SDK's peer address and user agent."""
    line = console_line(
        _Span(
            "llm.answer",
            {
                "claim.id": "claim-1",
                "llm.provider": "fake",
                "llm.estimated_cost_usd": 0.0,
                "net.peer.ip": "172.18.0.1",
            },
        )
    )
    assert line is not None
    assert line.startswith("trace llm.answer 5ms")
    assert "claim.id=claim-1" in line
    assert "llm.provider=fake" in line
    assert "172.18.0.1" not in line


def test_console_line_skips_streamed_body_chunks() -> None:
    """Each SSE token is its own ASGI span. Printing those is the wall of JSON."""
    assert console_line(_Span("POST http send", {"asgi.event.type": "http.response.body"})) is None
