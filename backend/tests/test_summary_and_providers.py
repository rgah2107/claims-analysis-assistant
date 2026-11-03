"""Schema repair and provider fallback.

The repair policy is: one new attempt that sees the validation error, then a
visible schema_error. Fallback happens only when a provider fails before it
emits text.
"""

from __future__ import annotations

import json

import pytest
from app.errors import ProviderError
from app.providers import LLMRouter
from app.schemas import ClaimSummary
from app.summary import parse_json_object, summarize


class Scripted:
    """Returns canned strings and records nothing else."""

    name = "scripted"

    def __init__(self, responses: list[str], *, fail_before_text: bool = False) -> None:
        self.responses = list(responses)
        self.fail_before_text = fail_before_text

    def complete(self, prompt: str, *, as_json: bool = False) -> str:
        if self.fail_before_text:
            raise ProviderError("down")
        if not self.responses:
            raise ProviderError("no scripted response left")
        return self.responses.pop(0)

    def stream(self, prompt: str):
        if self.fail_before_text:
            raise ProviderError("down")
        yield self.complete(prompt)


def test_invalid_json_is_repaired_once() -> None:
    """A bad confidence value is rejected, and the second payload is accepted."""
    router = LLMRouter(
        [
            Scripted(
                [
                    '{"confidence": "very high"}',
                    json.dumps(_valid()),
                ]
            )
        ]
    )
    summary, status = summarize(router, "What is the policy", [])
    assert status == "repaired"
    assert summary is not None
    assert summary.confidence == "low"


def test_a_provider_failure_is_not_called_a_schema_error() -> None:
    """Gemini being down is not invalid JSON. The caller must say the provider failed."""
    router = LLMRouter([Scripted([], fail_before_text=True)])
    summary, status = summarize(router, "What is the policy", [])
    assert summary is None
    assert status == "provider_error"


def test_two_invalid_payloads_become_schema_error() -> None:
    """After the repair attempt the caller must not pretend there is a summary."""
    router = LLMRouter([Scripted(['{"confidence": "nope"}', '{"confidence": "still-nope"}'])])
    summary, status = summarize(router, "What is the policy", [])
    assert summary is None
    assert status == "schema_error"


def test_a_valid_first_payload_is_not_sent_back_for_repair() -> None:
    """The happy path consumes a single response."""
    scripted = Scripted([json.dumps(_valid()), '{"confidence": "high"}'])
    summary, status = summarize(LLMRouter([scripted]), "What is the policy", [])
    assert status == "ok"
    assert summary == ClaimSummary.model_validate(_valid())
    assert scripted.responses  # the second canned payload was not used


def test_markdown_fence_around_json_is_accepted() -> None:
    """Models often wrap JSON in fences. The parser strips one pair."""
    parsed = parse_json_object('```json\n{"confidence": "low"}\n```')
    assert parsed["confidence"] == "low"


def test_nested_contradiction_statements_become_strings() -> None:
    """Gemma wraps each statement as an object. The card expects strings."""
    raw = json.dumps(
        {
            "policy_number": "POL-NS-2024-1187",
            "dates": ["2024-03-01"],
            "applicable_coverage": "water damage",
            "confidence": "medium",
            "contradictions": [
                {
                    "topic": "indemnity limit for water damage",
                    "statements": [
                        {
                            "content": "The indemnity limit for water damage is 2000 EUR per claim.",
                            "citation_chunk_ids": ["chunk-a"],
                        },
                        {
                            "content": "The indemnity limit for water damage is changed to 5000 EUR per claim.",
                            "citation_chunk_ids": ["chunk-b"],
                        },
                    ],
                }
            ],
        }
    )
    summary = ClaimSummary.model_validate(parse_json_object(raw))
    contradiction = summary.contradictions[0]
    assert contradiction.statements[0].startswith("The indemnity limit")
    assert contradiction.citation_chunk_ids == ["chunk-a", "chunk-b"]


def test_router_falls_back_only_before_the_first_token() -> None:
    """The second provider answers when the first one never started."""
    down = Scripted([], fail_before_text=True)
    down.name = "down"
    backup = Scripted(["respuesta concreta"])
    backup.name = "backup"
    router = LLMRouter([down, backup])
    assert "".join(router.stream("pregunta")) == "respuesta concreta"
    assert router.last_provider == "backup"


def test_router_does_not_restart_after_a_partial_answer() -> None:
    """A cut stream is an error, not an invitation to append another provider."""

    class Partial:
        name = "partial"

        def complete(self, prompt: str, *, as_json: bool = False) -> str:
            return "unused"

        def stream(self, prompt: str):
            yield "Starts "
            raise ProviderError("connection dropped")

    router = LLMRouter([Partial(), Scripted(["other"])])
    collected = []
    with pytest.raises(ProviderError):
        for piece in router.stream("question"):
            collected.append(piece)
    assert collected == ["Starts "]


def test_fake_embeddings_are_deterministic_and_accent_insensitive() -> None:
    """Demo retrieval has to be stable from one test run to the next."""
    from app.embeddings import FakeEmbeddings

    embedder = FakeEmbeddings()
    first = embedder.embed(["Café policy"])[0]
    second = embedder.embed(["cafe policy"])[0]
    assert first == second
    assert len(first) == 384
    norm = sum(value * value for value in first) ** 0.5
    assert norm == pytest.approx(1.0)


def test_gemini_3_8_request_skips_thoughts_and_does_not_send_temperature() -> None:
    """3.8 Flash puts reasoning in a thought part and rejects temperature later."""
    from app.providers import _gemini_generation, _gemini_text

    generation = _gemini_generation(as_json=True)
    assert "temperature" not in generation
    assert generation["thinkingConfig"] == {"thinkingLevel": "low"}
    assert generation["responseMimeType"] == "application/json"
    raw = json.dumps(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "razonamiento interno"},
                            {"text": "POL-NS-2024-1187"},
                        ]
                    }
                }
            ]
        }
    )
    assert _gemini_text(raw) == "POL-NS-2024-1187"


def test_local_provider_does_not_require_a_key_or_json_mode() -> None:
    """Bionic speaks chat completions and often rejects response_format."""
    from app.config import Settings
    from app.providers import _build_provider

    settings = Settings(
        local_llm_model="gemma-3-12b-it",
        local_llm_base_url="http://host.docker.internal:1235/v1",
        local_llm_api_key="",
    )
    provider = _build_provider("local", settings)
    payload = provider._payload("What is the limit?", stream=False, as_json=True)
    assert provider.name == "local"
    assert payload["model"] == "gemma-3-12b-it"
    assert "response_format" not in payload
    assert provider._headers() == {}
    assert provider._timeout == 180.0


def _valid() -> dict:
    """The smallest object ClaimSummary accepts."""
    return {
        "policy_number": None,
        "dates": [],
        "applicable_coverage": None,
        "confidence": "low",
        "contradictions": [],
    }
