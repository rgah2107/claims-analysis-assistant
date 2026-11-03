/**
 * The document panel. In-progress rows are polled by the parent.
 * A failed row shows the stored reason. An inactive row is a previous version
 * whose citations still resolve, but which is no longer searched.
 */

import type { ClaimDocument } from "../types";

const LABELS: Record<string, string> = {
  queued: "queued",
  extracting: "extracting",
  chunking: "chunking",
  embedding: "embeddings",
  indexing: "indexing",
  ready: "ready",
  failed: "failed",
};

export function DocumentList({ documents }: { documents: ClaimDocument[] }) {
  if (documents.length === 0) {
    return <p className="muted">No documents on this claim yet.</p>;
  }
  return (
    <ul className="documents">
      {documents.map((document) => (
        <li key={document.id} className={document.status === "failed" ? "failed" : ""}>
          <div className="document-row">
            <span className="filename">{document.filename}</span>
            <span className={`status status-${document.status}`}>
              {LABELS[document.status] ?? document.status}
            </span>
          </div>
          <p className="meta">
            version {document.version}
            {document.active ? "" : " · replaced"}
            {document.document_date ? ` · ${document.document_date}` : ""}
          </p>
          {document.status === "failed" && document.error_message ? (
            <p className="error-text">{document.error_message}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
