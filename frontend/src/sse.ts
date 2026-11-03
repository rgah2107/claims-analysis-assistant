/**
 * Server-Sent Events parser and the chat reducer.
 *
 * The browser delivers arbitrary slices of the stream. A frame can be cut in
 * the middle, so the parser keeps the unfinished tail and only emits frames
 * that ended with a blank line. The reducer is pure: tests feed it events
 * without opening a network connection.
 */

import type { ChatTurn, Citation, ClaimSummary, Outcome } from "./types";

export interface SseEvent {
  event: string;
  data: string;
}

/** Split complete frames. `rest` is the incomplete tail to prepend next time. */
export function parseSseBuffer(buffer: string): { events: SseEvent[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const events = parts.filter((part) => part.trim().length > 0).map(parseFrame);
  return { events, rest };
}

/**
 * Fold one SSE event into the turn.
 * Citations arrive before tokens, so the source list can render while the
 * sentence is still growing. Unknown events are ignored.
 */
export function applySseEvent(turn: ChatTurn, event: string, data: SsePayload): ChatTurn {
  switch (event) {
    case "start":
      return { ...turn, answerId: String(data.answer_id ?? turn.answerId) };
    case "token":
      return { ...turn, text: turn.text + String(data.text ?? "") };
    case "citation":
      return { ...turn, citations: [...turn.citations, data as unknown as Citation] };
    case "summary":
      return { ...turn, summary: data as unknown as ClaimSummary };
    case "insufficient_evidence":
      return {
        ...turn,
        outcome: "insufficient_evidence",
        error: String(data.message ?? ""),
        text: turn.text || String(data.message ?? ""),
      };
    case "schema_error":
      return { ...turn, outcome: "schema_error", error: String(data.message ?? "") };
    case "error":
      return { ...turn, outcome: "provider_error", error: String(data.message ?? "") };
    case "done":
      return {
        ...turn,
        answerId: String(data.answer_id ?? turn.answerId),
        outcome: (data.outcome as Outcome) || turn.outcome,
      };
    default:
      return turn;
  }
}

/** Read the event name and the data line. JSON colons stay inside the data. */
function parseFrame(frame: string): SseEvent {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  return { event, data: dataLines.join("\n") };
}

interface SsePayload {
  answer_id?: string;
  text?: string;
  message?: string;
  outcome?: string;
  [key: string]: unknown;
}
