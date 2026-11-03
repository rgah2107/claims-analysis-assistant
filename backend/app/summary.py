"""Validate the structured summary, with one repair attempt.

Policy, in order:

1. Ask the provider for JSON and validate it with ``ClaimSummary``.
2. If validation fails, ask once more, including the error text and the same documents.
3. If the second payload is still invalid, return no summary and the caller emits
   ``schema_error``. The prose answer, if it was already streamed, is kept and
   marked as not accompanied by a valid summary.

An empty abstention summary is built in code and validated the same way. The
model is not asked to invent fields when the evidence gate already refused.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.errors import ProviderError
from app.grounding import blocks_from_hits, relevant_contradictions, summary_payload
from app.prompts import build_repair_prompt, build_summary_prompt
from app.providers import LLMRouter
from app.retrieval import RetrievedChunk
from app.schemas import ClaimSummary

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def empty_summary(question: str, hits: list[RetrievedChunk]) -> ClaimSummary:
    """A valid summary for a refused answer. Coverage stays null on purpose."""
    payload = summary_payload(question, blocks_from_hits(hits), abstained=True)
    return ClaimSummary.model_validate(payload)


def summarize(router: LLMRouter, question: str, hits: list[RetrievedChunk]) -> tuple[ClaimSummary | None, str]:
    """Return ``(summary, status)`` where status is ok, repaired, or schema_error."""
    prompt = build_summary_prompt(question, hits)
    try:
        first = router.complete(prompt, as_json=True)
    except ProviderError:
        return None, "schema_error"
    try:
        return _validate(first, question), "ok"
    except (ValidationError, ValueError) as first_error:
        repair = build_repair_prompt(prompt, first, str(first_error))
        try:
            second = router.complete(repair, as_json=True)
            return _validate(second, question), "repaired"
        except (ProviderError, ValidationError, ValueError):
            return None, "schema_error"


def parse_json_object(raw: str) -> dict:
    """Accept a raw JSON object or a payload wrapped in a markdown fence."""
    cleaned = FENCE_RE.sub("", raw.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the model did not return a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("the model returned JSON that is not an object")
    return parsed


def _validate(raw: str, question: str) -> ClaimSummary:
    """Parse, validate, and drop contradictions the question did not ask about."""
    summary = ClaimSummary.model_validate(parse_json_object(raw))
    kept = relevant_contradictions(
        question, [item.model_dump() for item in summary.contradictions]
    )
    if len(kept) == len(summary.contradictions):
        return summary
    return ClaimSummary.model_validate({**summary.model_dump(), "contradictions": kept})
