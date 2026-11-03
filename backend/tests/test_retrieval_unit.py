"""Rank fusion and the refusal gate. These do not need a database."""

from __future__ import annotations

from app.retrieval import RetrievedChunk, evidence_is_sufficient, reciprocal_rank_fusion


def test_rrf_rewards_a_document_that_appears_in_both_lists() -> None:
    """c3 is only third in the vector list, but first in the lexical list, so it wins."""
    fused = reciprocal_rank_fusion([["c1", "c2", "c3"], ["c3", "c4"]], k=60)
    assert fused[0][0] == "c3"
    scores = dict(fused)
    # Rank 1 contributes 1/61. c3 is rank 3 and rank 1.
    assert scores["c3"] == _rank_score(3) + _rank_score(1)
    assert scores["c1"] == _rank_score(1)


def test_rrf_breaks_ties_by_id_so_the_order_is_stable() -> None:
    """Equal scores must not depend on dict order."""
    first = reciprocal_rank_fusion([["b", "a"]], k=60)
    second = reciprocal_rank_fusion([["b", "a"]], k=60)
    assert first == second


def test_empty_hits_are_not_enough_evidence() -> None:
    """No chunks means the assistant must refuse."""
    assert evidence_is_sufficient("what is the deductible", []) is False


def test_unrelated_top_hit_is_not_enough_evidence() -> None:
    """A nearest neighbour that does not share content words is still a refusal."""
    hit = _hit("The annual premium is paid by bank transfer.")
    assert evidence_is_sufficient("who is the surveyor for the fire", [hit]) is False


def test_shared_content_words_are_enough_evidence() -> None:
    """Overlap, not the raw fused score, opens the gate."""
    hit = _hit("The policy number is POL-NS-2024-1187.")
    assert evidence_is_sufficient("What is the policy number", [hit]) is True


def _hit(body: str) -> RetrievedChunk:
    """A hit with only the fields the gate reads."""
    return RetrievedChunk(
        chunk_id="c",
        document_id="d",
        claim_id="claim",
        filename="policy.pdf",
        version=1,
        document_type="policy",
        document_date="2024-03-01",
        page_start=1,
        page_end=1,
        section_title=None,
        body=body,
        fused_score=0.02,
        cosine=0.2,
    )


def _rank_score(rank: int) -> float:
    """Score contribution of a 1-based rank when k is 60."""
    return 1.0 / (60 + rank)
