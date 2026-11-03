"""Extractive answers and summaries from retrieved chunks.

Demo mode does not ask a generative model to invent a sentence. It quotes the
retrieved text, drops lines that look like instructions, and reports a
contradiction when two sources name different euro limits. The same functions
build the JSON that the fake provider returns, so the Pydantic check still runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.retrieval import RetrievedChunk, significant_tokens

BLOCK_RE = re.compile(
    r"<untrusted_document\s+(?P<attrs>[^>]*)>(?P<body>.*?)</untrusted_document>",
    re.DOTALL,
)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
INSTRUCTION_RE = re.compile(
    r"\b(ignore\b|disregard\b|system prompt|you are now|instrucciones anteriores|"
    r"ignora\b|olvida\b)\b",
    re.IGNORECASE,
)
POLICY_RE = re.compile(r"\bPOL-[A-Z0-9]+(?:-[A-Z0-9]+)+\b")
DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
AMOUNT_RE = re.compile(r"(\d[\d.]*)\s*EUR", re.IGNORECASE)
LIMIT_RE = re.compile(r"\blimit\b", re.IGNORECASE)
DEDUCTIBLE_RE = re.compile(r"\bdeductible\b", re.IGNORECASE)


@dataclass(frozen=True)
class SourceBlock:
    """One document block, either parsed from a prompt or copied from a hit."""

    chunk_id: str
    filename: str
    date: str
    body: str


def blocks_from_hits(hits: list[RetrievedChunk]) -> list[SourceBlock]:
    """Adapt retrieval hits to the structure the extractors expect."""
    return [
        SourceBlock(
            chunk_id=hit.chunk_id,
            filename=hit.filename,
            date=hit.document_date or "",
            body=hit.body,
        )
        for hit in hits
    ]


def blocks_from_prompt(prompt: str) -> list[SourceBlock]:
    """Read the document tags out of a prompt. Instructions outside the tags are ignored."""
    blocks: list[SourceBlock] = []
    for match in BLOCK_RE.finditer(prompt):
        attributes = dict(ATTR_RE.findall(match.group("attrs")))
        blocks.append(
            SourceBlock(
                chunk_id=attributes.get("chunk_id", ""),
                filename=attributes.get("filename", ""),
                date=attributes.get("date", ""),
                body=match.group("body").strip(),
            )
        )
    return blocks


def question_from_prompt(prompt: str) -> str:
    """Pull the question line out of a prompt built by ``prompts.py``."""
    match = re.search(r"Question:\n(.+?)\n\n", prompt, re.DOTALL)
    return match.group(1).strip() if match else ""


def extractive_answer(question: str, blocks: list[SourceBlock]) -> str:
    """Quote sentences that cover the question and note a limit conflict when one exists.

    A question about the current limit and a question about the original limit
    must not return the same paragraph. The current question leads with the
    later amount. The original question leads with the earlier amount.
    """
    distinguished = _distinguished_limit_answer(question, blocks)
    if distinguished:
        return distinguished
    ranked = _ranked_sentences(question, blocks)
    if not ranked:
        return "I cannot answer from the documents on this claim."
    text = " ".join(_cover_query_tokens(question, ranked))
    note = contradiction_note(blocks)
    if note and _question_is_about_limit(question):
        text = f"{text} {note}"
    return text.strip()


def summary_payload(question: str, blocks: list[SourceBlock], *, abstained: bool) -> dict:
    """Build a dict that ``ClaimSummary`` can validate without a model call."""
    contradictions = [] if abstained else relevant_contradictions(question, contradiction_payload(blocks))
    coverage = None if abstained else _coverage_sentence(question, blocks, contradictions)
    if abstained:
        confidence = "low"
    elif contradictions:
        confidence = "medium"
    elif coverage:
        confidence = "high"
    else:
        confidence = "low"
    return {
        "policy_number": _policy_number(blocks),
        "dates": _dates(blocks),
        "applicable_coverage": coverage,
        "confidence": confidence,
        "contradictions": contradictions,
    }


def contradiction_note(blocks: list[SourceBlock]) -> str | None:
    """Sentence naming both limits and which document is later."""
    found = _amounts_for(blocks, LIMIT_RE)
    unique = list(dict.fromkeys(amount for _block, amount in found))
    if len(unique) < 2:
        return None
    later_block, later_amount = max(found, key=lambda item: item[0].date)
    details = "; ".join(
        f"{block.filename} ({block.date or 'undated'}) states {amount} EUR"
        for block, amount in _first_of_each_amount(found)
    )
    return (
        f"The documents disagree on the limit: {details}. "
        f"The later document prevails: {later_block.filename} with {later_amount} EUR. "
        "Both sources stay cited."
    )


def relevant_contradictions(question: str, contradictions: list[dict]) -> list[dict]:
    """Drop a disagreement the question did not ask about.

    Both files mention the policy, so a question about the insured person
    retrieves the endorsement too. The 2000/5000 limit is still in those
    chunks. It is not a conflict for that question, and the warning should
    not appear.
    """
    return [item for item in contradictions if _contradiction_matches(question, str(item.get("topic", "")))]


def contradiction_payload(blocks: list[SourceBlock]) -> list[dict]:
    """Schema-shaped contradictions for the euro limit and the deductible."""
    payloads: list[dict] = []
    for topic, pattern in (("indemnity limit", LIMIT_RE), ("deductible", DEDUCTIBLE_RE)):
        found = _amounts_for(blocks, pattern)
        unique = list(dict.fromkeys(amount for _block, amount in found))
        if len(unique) < 2:
            continue
        statements = [
            f"{block.filename} ({block.date or 'undated'}): {amount} EUR"
            for block, amount in _first_of_each_amount(found)
        ]
        payloads.append(
            {
                "topic": topic,
                "statements": statements,
                "citation_chunk_ids": list(
                    dict.fromkeys(block.chunk_id for block, _amount in found if block.chunk_id)
                ),
            }
        )
    return payloads


def _coverage_sentence(question: str, blocks: list[SourceBlock], contradictions: list[dict]) -> str | None:
    """The sentence the summary calls the applicable coverage."""
    lead = _limit_lead_sentence(question, blocks)
    if lead:
        return lead
    if contradictions and _question_is_about_limit(question):
        return contradiction_note(blocks)
    ranked = _ranked_sentences(question, blocks)
    if not ranked or ranked[0][1] <= 0:
        return None
    return ranked[0][0]


def _cover_query_tokens(
    question: str, ranked: list[tuple[str, float]], limit: int = 3
) -> list[str]:
    """Keep a sentence only when it adds a question token the earlier quotes missed.

    Overlap ties are common: "policy" appears in many lines, "term" in one.
    Taking the first two ties alphabetically would quote the policy number and
    drop the date the adjuster asked for. Three sentences is enough for one
    clause plus the line that names the missing term.
    """
    query = significant_tokens(question)
    chosen: list[str] = []
    covered: set[str] = set()
    for sentence, score in ranked:
        if score <= 0:
            continue
        added = (significant_tokens(sentence) & query) - covered
        if not added:
            continue
        chosen.append(sentence)
        covered.update(added)
        if covered >= query or len(chosen) >= limit:
            break
    return chosen


def _ranked_sentences(question: str, blocks: list[SourceBlock]) -> list[tuple[str, float]]:
    """Score quotable sentences by overlap with the question. Highest first."""
    query_tokens = significant_tokens(question)
    if not query_tokens:
        return []
    scored: list[tuple[str, float]] = []
    for block in blocks:
        for sentence in _sentences(block.body):
            if INSTRUCTION_RE.search(sentence):
                continue
            overlap = len(query_tokens & significant_tokens(sentence)) / len(query_tokens)
            if overlap > 0:
                scored.append((sentence.strip(), overlap))
    scored.sort(key=lambda item: (-item[1], item[0]))
    # Keep the first copy of each sentence.
    seen: set[str] = set()
    unique: list[tuple[str, float]] = []
    for sentence, score in scored:
        if sentence in seen:
            continue
        seen.add(sentence)
        unique.append((sentence, score))
    return unique


def _amounts_for(blocks: list[SourceBlock], topic: re.Pattern[str]) -> list[tuple[SourceBlock, str]]:
    """Euro amounts that appear in a sentence about ``topic``."""
    found: list[tuple[SourceBlock, str]] = []
    for block in blocks:
        for sentence in _sentences(block.body):
            if not topic.search(sentence):
                continue
            for amount in AMOUNT_RE.findall(sentence):
                found.append((block, amount))
    return found


def _first_of_each_amount(
    found: list[tuple[SourceBlock, str]],
) -> list[tuple[SourceBlock, str]]:
    """One source per distinct amount, in the order the amounts were found."""
    chosen: list[tuple[SourceBlock, str]] = []
    seen: set[str] = set()
    for block, amount in found:
        if amount in seen:
            continue
        seen.add(amount)
        chosen.append((block, amount))
    return chosen


def _policy_number(blocks: list[SourceBlock]) -> str | None:
    """First policy id of the form POL-... in the retrieved text."""
    for block in blocks:
        match = POLICY_RE.search(block.body)
        if match:
            return match.group(0)
    return None


def _dates(blocks: list[SourceBlock]) -> list[str]:
    """Dates in the order they appear, without duplicates."""
    ordered: list[str] = []
    for block in blocks:
        for match in DATE_RE.findall(block.body):
            if match not in ordered:
                ordered.append(match)
    return ordered


def _sentences(text: str) -> list[str]:
    """Split on punctuation or newlines and drop empty pieces."""
    return [part.strip() for part in SENTENCE_RE.split(text) if part.strip()]


EARLIER_RE = re.compile(r"\b(original|before|previous|prior|earlier)\b", re.IGNORECASE)


def _distinguished_limit_answer(question: str, blocks: list[SourceBlock]) -> str | None:
    """Different prose for the operative limit and for the amount it replaced."""
    sides = _limit_sides(blocks)
    if sides is None or not _question_names_the_limit(question):
        return None
    earlier_block, earlier_amount, earlier_sentence, later_block, later_amount, later_sentence = sides
    if EARLIER_RE.search(question):
        return (
            f"{earlier_sentence} "
            f"That is the original limit, in {earlier_block.filename} "
            f"({earlier_block.date or 'undated'}), before {later_block.filename} "
            f"({later_block.date or 'undated'}) changed it to {later_amount} EUR."
        )
    note = contradiction_note(blocks)
    return f"{later_sentence} {note}".strip()


def _limit_lead_sentence(question: str, blocks: list[SourceBlock]) -> str | None:
    """The one sentence that answers this limit question, without the conflict note."""
    sides = _limit_sides(blocks)
    if sides is None or not _question_names_the_limit(question):
        return None
    _earlier_block, _earlier_amount, earlier_sentence, _later_block, _later_amount, later_sentence = sides
    if EARLIER_RE.search(question):
        return earlier_sentence
    return later_sentence


def _limit_sides(
    blocks: list[SourceBlock],
) -> tuple[SourceBlock, str, str, SourceBlock, str, str] | None:
    """Earlier and later euro limits, each with the sentence that states it."""
    pairs = _first_of_each_amount(_amounts_for(blocks, LIMIT_RE))
    if len(pairs) < 2:
        return None
    earlier_block, earlier_amount = min(pairs, key=lambda item: (item[0].date or "", item[1]))
    later_block, later_amount = max(pairs, key=lambda item: (item[0].date or "", item[1]))
    if earlier_amount == later_amount:
        return None
    earlier_sentence = _sentence_with_amount(earlier_block, earlier_amount)
    later_sentence = _sentence_with_amount(later_block, later_amount)
    if not earlier_sentence or not later_sentence:
        return None
    return (
        earlier_block,
        earlier_amount,
        earlier_sentence,
        later_block,
        later_amount,
        later_sentence,
    )


def _sentence_with_amount(block: SourceBlock, amount: str) -> str | None:
    """The sentence in this block that states this euro amount as a limit."""
    for sentence in _sentences(block.body):
        if LIMIT_RE.search(sentence) and amount in AMOUNT_RE.findall(sentence):
            return sentence.strip()
    return None


def _question_names_the_limit(question: str) -> bool:
    """True when the question itself says limit or indemnity, not only water."""
    return bool(significant_tokens(question) & {"limit", "indemnity"})


def _question_is_about_limit(question: str) -> bool:
    """True when the question is about the insured limit rather than another clause."""
    return _contradiction_matches(question, "indemnity limit")


def _contradiction_matches(question: str, topic: str) -> bool:
    """True when the question's content words are about this disagreement."""
    tokens = significant_tokens(question)
    topic_tokens = significant_tokens(topic)
    if "deductible" in topic_tokens:
        return bool(tokens & {"deductible"})
    if topic_tokens & {"limit", "indemnity"}:
        # "water" alone is not enough. The deductible question also says water.
        return bool(tokens & {"limit", "indemnity"})
    return bool(tokens & topic_tokens)
