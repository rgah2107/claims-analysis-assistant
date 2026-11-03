/**
 * What the adjuster sees: a failed document explains itself, and a citation
 * click reveals the fragment that grounded the answer.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChatPanel } from "../components/ChatPanel";
import { DocumentList } from "../components/DocumentList";
import type { ChatTurn, ClaimDocument } from "../types";

const failed: ClaimDocument = {
  id: "d1",
  filename: "roto.pdf",
  version: 1,
  status: "failed",
  error_message: "PDF corrupto: no se pudo leer",
  active: true,
  document_type: "unknown",
  document_date: null,
  size_bytes: 20,
};

const turn: ChatTurn = {
  answerId: "a1",
  question: "What is the limit?",
  text: "The limit is 2000 EUR.",
  outcome: "answered",
  error: null,
  feedback: null,
  summary: {
    policy_number: "POL-NS-2024-1187",
    dates: ["01/03/2024"],
    applicable_coverage: "Water damage",
    confidence: "high",
    contradictions: [],
  },
  citations: [
    {
      chunk_id: "c1",
      document_id: "d1",
      filename: "home-policy.pdf",
      version: 1,
      page_start: 1,
      page_end: 1,
      section: "CLAUSE 4. Water damage",
      quote: "The indemnity limit for water damage is 2000 EUR per claim.",
    },
  ],
};

describe("DocumentList", () => {
  it("shows the failure reason stored on the document", () => {
    render(<DocumentList documents={[failed]} />);
    expect(screen.getByText("roto.pdf")).toBeInTheDocument();
    expect(screen.getByText("PDF corrupto: no se pudo leer")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });
});

describe("ChatPanel", () => {
  it("reveals the cited fragment when the adjuster clicks the citation", async () => {
    render(
      <ChatPanel
        turns={[turn]}
        question=""
        asking={false}
        onQuestion={() => undefined}
        onAsk={() => undefined}
        onFeedback={() => undefined}
      />,
    );
    expect(screen.queryByText(/2000 EUR per claim/)).not.toBeInTheDocument();
    screen.getByRole("button", { name: /View excerpt/ }).click();
    expect(await screen.findByText(/2000 EUR per claim/)).toBeInTheDocument();
    expect(screen.getByText("CLAUSE 4. Water damage")).toBeInTheDocument();
  });
});
