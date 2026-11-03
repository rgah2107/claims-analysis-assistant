/**
 * HTTP calls. Paths are relative so the same build works behind nginx and the Vite proxy.
 */

import { applySseEvent, parseSseBuffer } from "./sse";
import type { ChatTurn, ClaimDocument } from "./types";
import { emptyTurn } from "./types";

export interface Health {
  status: string;
  llm_provider: string;
  embedding_provider: string;
  database: string;
}

export async function fetchHealth(): Promise<Health> {
  /** Demo mode is visible when both providers are `fake`. */
  const response = await fetch("/api/health");
  if (!response.ok && response.status !== 503) {
    throw new Error("Could not read the service status.");
  }
  return response.json();
}

export async function uploadDocuments(claimNumber: string, files: File[]): Promise<void> {
  /** Returns when the files are stored, not when ingest has finished. */
  const body = new FormData();
  for (const file of files) {
    body.append("files", file);
  }
  const response = await fetch(`/api/claims/${encodeURIComponent(claimNumber)}/documents`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof detail.detail === "string" ? detail.detail : "Upload failed.");
  }
}

export async function fetchDocuments(claimNumber: string): Promise<ClaimDocument[]> {
  /** The list the UI polls while any document is still in progress. */
  const response = await fetch(`/api/claims/${encodeURIComponent(claimNumber)}/documents`);
  if (!response.ok) {
    throw new Error("Could not load the documents.");
  }
  const payload = await response.json();
  return payload.documents as ClaimDocument[];
}

export async function askQuestion(
  claimNumber: string,
  question: string,
  onTurn: (turn: ChatTurn) => void,
): Promise<void> {
  /**
   * Stream one answer. `onTurn` is called with the whole turn after every event
   * so React state stays a snapshot, not a mutated object.
   */
  const response = await fetch(`/api/claims/${encodeURIComponent(claimNumber)}/questions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok || response.body === null) {
    throw new Error("Could not open the answer stream.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let turn = emptyTurn(question);
  onTurn(turn);
  while (true) {
    const step = await reader.read();
    if (step.done) {
      break;
    }
    buffer += decoder.decode(step.value, { stream: true });
    const parsed = parseSseBuffer(buffer);
    buffer = parsed.rest;
    for (const frame of parsed.events) {
      turn = applySseEvent(turn, frame.event, JSON.parse(frame.data));
      onTurn(turn);
    }
  }
}

export async function sendFeedback(answerId: string, useful: boolean): Promise<void> {
  /** A vote does not need the stream to still be open. */
  const response = await fetch(`/api/answers/${encodeURIComponent(answerId)}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ useful }),
  });
  if (!response.ok) {
    throw new Error("Could not save the feedback.");
  }
}
