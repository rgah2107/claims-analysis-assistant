/**
 * The stream parser and the reducer. These are the behaviours the chat test
 * would otherwise need a live server to prove.
 */

import { describe, expect, it } from "vitest";
import { applySseEvent, parseSseBuffer } from "../sse";
import { emptyTurn } from "../types";

describe("parseSseBuffer", () => {
  it("keeps a frame that has not finished yet", () => {
    const partial = parseSseBuffer("event: token\ndata: {\"text\": \"Hol");
    expect(partial.events).toEqual([]);
    expect(partial.rest).toContain("Hol");
  });

  it("emits a finished frame and keeps the next incomplete one", () => {
    const parsed = parseSseBuffer(
      'event: token\ndata: {"text": "Hola"}\n\nevent: citation\ndata: {"chunk_id": "c1"}',
    );
    expect(parsed.events).toHaveLength(1);
    expect(parsed.events[0].event).toBe("token");
    expect(parsed.rest).toContain("citation");
  });
});

describe("applySseEvent", () => {
  it("appends tokens and then opens a citation", () => {
    let turn = emptyTurn("What is the policy?");
    turn = applySseEvent(turn, "start", { answer_id: "a1" });
    turn = applySseEvent(turn, "token", { text: "POL-" });
    turn = applySseEvent(turn, "token", { text: "NS" });
    turn = applySseEvent(turn, "citation", {
      chunk_id: "c1",
      document_id: "d1",
      filename: "policy.pdf",
      version: 1,
      page_start: 2,
      page_end: 2,
      section: "CLAUSE 4",
      quote: "The limit is 2000 EUR.",
    });
    turn = applySseEvent(turn, "done", { answer_id: "a1", outcome: "answered" });
    expect(turn.text).toBe("POL-NS");
    expect(turn.citations[0].page_start).toBe(2);
    expect(turn.citations[0].quote).toContain("2000 EUR");
    expect(turn.outcome).toBe("answered");
  });

  it("records a refusal instead of an invented answer", () => {
    const turn = applySseEvent(emptyTurn("Does it cover jewelry?"), "insufficient_evidence", {
      message: "I cannot answer from the documents on this claim.",
    });
    expect(turn.outcome).toBe("insufficient_evidence");
    expect(turn.text).toContain("I cannot answer");
  });
});
