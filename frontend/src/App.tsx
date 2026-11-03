/**
 * One claim workspace: upload on the left, streaming chat on the right.
 * Documents are polled every two seconds while any of them is still running.
 */

import { useEffect, useState } from "react";
import { askQuestion, fetchDocuments, fetchHealth, sendFeedback, uploadDocuments, type Health } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { DocumentList } from "./components/DocumentList";
import type { ChatTurn, ClaimDocument } from "./types";

const TERMINAL = new Set(["ready", "failed"]);

export function App() {
  const [claimNumber, setClaimNumber] = useState("SIN-1001");
  const [activeClaim, setActiveClaim] = useState("SIN-1001");
  const [documents, setDocuments] = useState<ClaimDocument[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    // Poll only while a document is still running. The timer is chained from the
    // response, so a finished list does not keep requesting.
    let cancelled = false;
    let timer = 0;
    const load = () => {
      fetchDocuments(activeClaim)
        .then((rows) => {
          if (cancelled) {
            return;
          }
          setDocuments(rows);
          if (rows.some((document) => !TERMINAL.has(document.status))) {
            timer = window.setTimeout(load, 2000);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setDocuments([]);
          }
        });
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeClaim, refreshTick]);

  async function onUpload(fileList: FileList | null) {
    /** The request returns a job id. This view then polls until each file is ready or failed. */
    if (!fileList || fileList.length === 0) {
      return;
    }
    setError(null);
    setActiveClaim(claimNumber);
    try {
      await uploadDocuments(claimNumber, Array.from(fileList));
      setRefreshTick((value) => value + 1);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed");
    }
  }

  async function onAsk() {
    const trimmed = question.trim();
    if (trimmed.length < 3 || asking) {
      return;
    }
    setAsking(true);
    setError(null);
    setQuestion("");
    try {
      await askQuestion(activeClaim, trimmed, (turn) => {
        setTurns((current) => {
          const without = current.filter((item) => item.question !== turn.question || item.answerId);
          const existing = without.findIndex((item) => item.answerId && item.answerId === turn.answerId);
          if (turn.answerId && existing >= 0) {
            const next = without.slice();
            next[existing] = turn;
            return next;
          }
          const pending = without.findIndex((item) => item.question === turn.question && !item.answerId);
          if (pending >= 0) {
            const next = without.slice();
            next[pending] = turn;
            return next;
          }
          return [...without, turn];
        });
      });
    } catch (askError) {
      setError(askError instanceof Error ? askError.message : "Could not ask the question");
    } finally {
      setAsking(false);
    }
  }

  async function onFeedback(answerId: string, useful: boolean) {
    await sendFeedback(answerId, useful);
    setTurns((current) =>
      current.map((turn) =>
        turn.answerId === answerId ? { ...turn, feedback: useful ? "useful" : "not_useful" } : turn,
      ),
    );
  }

  const demo = health?.llm_provider === "fake" && health.embedding_provider === "fake";

  return (
    <>
      <header className="top">
        <div>
          <p className="brand">Norte Seguros</p>
          <h1>Claims assistant</h1>
        </div>
        <p className={demo ? "badge" : "badge live"}>
          {health === null ? "offline" : demo ? "demo mode" : `model ${health.llm_provider}`}
        </p>
      </header>
      <main className="layout">
        <section className="desk">
          <label htmlFor="claim">Claim</label>
          <div className="claim-row">
            <input
              id="claim"
              value={claimNumber}
              onChange={(event) => setClaimNumber(event.target.value)}
            />
            <button type="button" onClick={() => setActiveClaim(claimNumber)}>
              Open
            </button>
          </div>
          <label className="upload">
            Upload PDF or DOCX
            <input
              type="file"
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              multiple
              onChange={(event) => {
                void onUpload(event.target.files);
                event.target.value = "";
              }}
            />
          </label>
          {error ? <p className="error-text">{error}</p> : null}
          <DocumentList documents={documents} />
        </section>
        <ChatPanel
          turns={turns}
          question={question}
          asking={asking}
          onQuestion={setQuestion}
          onAsk={() => {
            void onAsk();
          }}
          onFeedback={(answerId, useful) => {
            void onFeedback(answerId, useful);
          }}
        />
      </main>
    </>
  );
}
