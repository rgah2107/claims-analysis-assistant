/** Shapes shared by the API client, the reducer, and the components. */

export type DocumentStatus =
  | "queued"
  | "extracting"
  | "chunking"
  | "embedding"
  | "indexing"
  | "ready"
  | "failed";

export interface ClaimDocument {
  id: string;
  filename: string;
  version: number;
  status: DocumentStatus | string;
  error_message: string | null;
  active: boolean;
  document_type: string;
  document_date: string | null;
  size_bytes: number;
}

export interface Citation {
  chunk_id: string;
  document_id: string;
  filename: string;
  version: number;
  page_start: number;
  page_end: number;
  section: string | null;
  quote: string;
}

export interface Contradiction {
  topic: string;
  statements: string[];
  citation_chunk_ids: string[];
}

export interface ClaimSummary {
  policy_number: string | null;
  dates: string[];
  applicable_coverage: string | null;
  confidence: "low" | "medium" | "high";
  contradictions: Contradiction[];
}

export type Outcome =
  | "pending"
  | "answered"
  | "insufficient_evidence"
  | "schema_error"
  | "provider_error";

/** One question and everything the stream has produced for it so far. */
export interface ChatTurn {
  answerId: string | null;
  question: string;
  text: string;
  citations: Citation[];
  summary: ClaimSummary | null;
  outcome: Outcome;
  error: string | null;
  feedback: "useful" | "not_useful" | null;
}

/** A turn that exists before the first SSE frame arrives. */
export function emptyTurn(question: string): ChatTurn {
  return {
    answerId: null,
    question,
    text: "",
    citations: [],
    summary: null,
    outcome: "pending",
    error: null,
    feedback: null,
  };
}
