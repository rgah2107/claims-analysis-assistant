/**
 * The conversation. Clicking a citation opens the stored fragment: file, page,
 * section, and the exact text that was retrieved. The quote is part of the
 * event, so it still matches the answer after a newer version of the file is uploaded.
 */

import { useState } from "react";
import type { ChatTurn, Citation } from "../types";

export function ChatPanel({
  turns,
  question,
  asking,
  onQuestion,
  onAsk,
  onFeedback,
}: {
  turns: ChatTurn[];
  question: string;
  asking: boolean;
  onQuestion: (value: string) => void;
  onAsk: () => void;
  onFeedback: (answerId: string, useful: boolean) => void;
}) {
  const [openCitation, setOpenCitation] = useState<Citation | null>(null);

  return (
    <section className="chat">
      <div className="turns">
        {turns.length === 0 ? (
          <p className="muted">Ask about the clauses in this claim.</p>
        ) : null}
        {turns.map((turn, index) => (
          <article key={turn.answerId ?? `pending-${index}`} className="turn">
            <h3>{turn.question}</h3>
            <p className="answer-text">{turn.text || (asking ? "Searching the evidence…" : "")}</p>
            {turn.outcome === "insufficient_evidence" ? (
              <p className="banner">Not enough evidence. No answer was invented.</p>
            ) : null}
            {turn.outcome === "schema_error" || turn.outcome === "provider_error" ? (
              <p className="error-text">{turn.error}</p>
            ) : null}
            {turn.summary && turn.summary.contradictions.length > 0 ? (
              <div className="banner warning">
                <strong>Conflicting documents.</strong>
                {turn.summary.contradictions.map((item) => (
                  <p key={item.topic}>
                    {item.topic}: {item.statements.join(" · ")}
                  </p>
                ))}
                <p>The later document prevails. Both citations stay visible.</p>
              </div>
            ) : null}
            {turn.summary ? (
              <dl className="summary">
                <div>
                  <dt>Policy</dt>
                  <dd>{turn.summary.policy_number ?? "—"}</dd>
                </div>
                <div>
                  <dt>Dates</dt>
                  <dd>{turn.summary.dates.length ? turn.summary.dates.join(", ") : "—"}</dd>
                </div>
                <div>
                  <dt>Coverage</dt>
                  <dd>{turn.summary.applicable_coverage ?? "—"}</dd>
                </div>
                <div>
                  <dt>Confidence</dt>
                  <dd>{turn.summary.confidence}</dd>
                </div>
              </dl>
            ) : null}
            {turn.citations.length > 0 ? (
              <ul className="citations">
                {turn.citations.map((citation) => (
                  <li key={citation.chunk_id}>
                    <button type="button" onClick={() => setOpenCitation(citation)}>
                      View excerpt · {citation.filename} · p. {citation.page_start}
                      {citation.page_end !== citation.page_start ? `–${citation.page_end}` : ""}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {turn.answerId && turn.outcome !== "pending" ? (
              <div className="feedback">
                <button
                  type="button"
                  disabled={turn.feedback !== null}
                  onClick={() => onFeedback(turn.answerId as string, true)}
                >
                  Useful
                </button>
                <button
                  type="button"
                  disabled={turn.feedback !== null}
                  onClick={() => onFeedback(turn.answerId as string, false)}
                >
                  Not useful
                </button>
                {turn.feedback ? <span className="muted">Recorded.</span> : null}
              </div>
            ) : null}
          </article>
        ))}
      </div>
      {openCitation ? (
        <aside className="fragment" aria-label="Cited excerpt">
          <header>
            <strong>{openCitation.filename}</strong>
            <span>
              version {openCitation.version} · page {openCitation.page_start}
            </span>
            <button type="button" onClick={() => setOpenCitation(null)}>
              Close
            </button>
          </header>
          {openCitation.section ? <p className="meta">{openCitation.section}</p> : null}
          <blockquote>{openCitation.quote}</blockquote>
        </aside>
      ) : null}
      <form
        className="ask"
        onSubmit={(event) => {
          event.preventDefault();
          onAsk();
        }}
      >
        <label htmlFor="question">Question</label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => onQuestion(event.target.value)}
          rows={3}
          placeholder="What is the water-damage limit?"
        />
        <button type="submit" disabled={asking || question.trim().length < 3}>
          {asking ? "Answering…" : "Ask"}
        </button>
      </form>
    </section>
  );
}
