# Decisions

This document answers the four open questions in the brief and records the trade-offs to defend in the interview. The same content, as spoken questions and answers, is in `docs/interview-prep.pdf`.

## How generative AI was used

The implementation was done with a coding agent in Cursor (Grok), from a plan reviewed before any code was written. The requested architecture was kept: FastAPI, Celery, Redis, PostgreSQL with pgvector, SSE, Pydantic, and a deterministic fake provider.

The Docker scope was corrected. The first plan left the test Postgres as a GitHub Actions service. The brief asks for at least one integration test against real PostgreSQL and pgvector, for example via Docker. The test now starts the `pgvector/pgvector` container with testcontainers, locally and in CI. Compose is still how a person runs the whole product, including the worker.

Using the fake provider as a safety net for Gemini was also rejected. A fake embedding mixed into a real index is not comparable, and a fake answer would look grounded.

## Docker

There are two uses, and they are not interchangeable.

`docker compose up --build` starts the API, the worker, Redis, PostgreSQL/pgvector, and the frontend. That is the environment a person sees. The API and the worker share the file volume because the worker has to read the bytes the API just stored. The Python client is `redis` 6.4 with `kombu` 5.7 pinned: kombu 5.7 rejects redis-py 5 when the connection opens. Tests use an in-memory broker, so that failure only appears when the Compose worker starts. Schema creation takes one transaction advisory lock. The API and every worker process call it at startup, and `CREATE EXTENSION IF NOT EXISTS` is not atomic across sessions: without the lock Postgres logs a unique violation on `vector` and then on the `claims` table. The containers start as root only long enough to give `/data` to uid 1000, then the API and the worker run as that user, so Celery does not warn that it is running as root.

`pytest` starts the `pgvector/pgvector:pg16` image by itself. It does not assume Compose is up. The hybrid-search, claim-isolation, and idempotency checks therefore run against the real engine. Chunking, RRF fusion, the retry policy, and Pydantic validation do not start Docker: they do not need a server to be true. Putting all of Compose inside every pytest run would slow the cycle and mix the frontend into a database test.

If Docker is not installed locally, the integration test skips with an explicit message. On GitHub Actions the runner has Docker, and that skip becomes a failure.

## Extraction: pypdf and python-docx, not Docling

Docling is the tool the brief names, and it is the right choice when layout, tables, and a difficult reading order matter. It also downloads models, lengthens the image, and puts a component on the ingest path that cannot be walked line by line in an interview.

pypdf returns text per page, which is the fact the assistant cites. python-docx reads paragraphs and treats a page break as a new page. A PDF with no text layer is not guessed: the document stays `failed` with the reason that scans are not supported. Extraction sits behind `extract_file`, so Docling can replace it without touching the worker.

## Chunking

Target about 1,200 characters, overlap about 200. Split on paragraphs, then sentences, and only then with a hard cut. The overlap is smaller than the target; otherwise the window would not move forward.

The clause heading (`CLAUSE`, `ARTICLE`, `ENDORSEMENT`) is stored as `section_title`. If a chunk continues a clause whose heading fell in the previous chunk, that heading is prepended to the indexed text. Search by clause name then does not depend on the heading and the limit landing in the same cut.

With more time I would measure several sizes against the evaluation set and keep the one that raises Recall@5 without hurting grounding. 1,200 / 200 is an explainable starting point, not a demonstrated optimum.

## Score fusion: RRF, k = 60

Vector search returns cosine distance. Full text returns `ts_rank_cd`. They are not on the same scale. Adding them with fixed weights would require calibrating those weights, and the evaluation corpus has twelve questions: that is not enough to tune a weight without fooling yourself.

Reciprocal Rank Fusion uses only the rank in each list. Rank 1 adds `1 / 61` when k is 60. A chunk that appears in both lists beats one that is first in only one. Ties break by id so the order is stable.

Each list contributes at most 20 candidates. Five are returned. The `claim_id` and `active = true` filter is inside both queries. `claim_id` is denormalized onto `chunks` so a forgotten join cannot widen the search to another claim.

Full text uses the `english` configuration, matching the policy text. The fake embedder still folds accents, because that is our code and not a generated column. Switching this column on a database that was created with `spanish` means deleting the `pgdata` volume and uploading again. `create_all` does not rewrite a generated column that already exists.

## When the assistant refuses

With a hashing embedder there is always a nearest neighbor, even when the claim says nothing about the topic. The evidence gate is therefore not the fused score. The top chunk must share at least 20% of the question's content words (or 10% if the cosine is also high). Otherwise the SSE stream emits `insufficient_evidence`, the summary has null coverage and `low` confidence, and the model is not called.

## Contradictions

A later letter can change a clause. The evaluation corpus does this: the policy says 2,000 EUR for water damage and the endorsement of 15/06/2024 says 5,000 EUR.

The system does not choose in silence. When the question is about that clause, the summary includes `contradictions` with both figures, the file, and the date, and both citations stay on screen. The UI shows a warning only for that question. The prose depends on what was asked. "What is the limit" leads with the later amount and says that document prevails. "What was the original limit" leads with the earlier amount and says the endorsement changed it. A question about the insured person can retrieve both files, because both name the policy, and it does not show the limit warning. The deductible is not a conflict because the endorsement says it does not change and does not state a second figure.

## Uploading a document again

Same bytes (same sha256 inside the claim): no new row is created and no new job is queued. The job id on that request points at the document that already existed.

Same name and different bytes: a new version. The previous one becomes `active = false`. Its chunks are kept, but search reads only the active ones. Citations already delivered store the `chunk_id`, the version, the file, and the chunk text. Opening an old citation shows the text that was used then, not the text of the new file.

## Structured summary

`ClaimSummary` forbids extra fields. Confidence can only be `low`, `medium`, or `high`. If the JSON does not validate, a second attempt is made with the validation error and the same documents. If the second attempt also fails, the event is `schema_error`: text that was already emitted is not presented as a valid summary. If the summary request fails before any JSON arrives, and the prose has already streamed, the card is built from the same chunks instead of showing a provider error for that second call.

In demo mode the "model" is extractive and the JSON comes from the chunks, but it goes through the same validation. When several sentences tie on overlap, a sentence is quoted only if it adds a question term the earlier quotes missed, up to three. That is why "what is the policy term" does not stop at the lines that only say "policy". Repair tests use a scripted provider, not the fake one, so they can force bad JSON.

## Providers

The interface is `complete` and `stream`. `LLM_PROVIDER` is one of `fake`, `gemini`, `openai`, or `local`.

`fake` quotes the retrieved sentences and builds the summary in code. It is the default when Compose has no `.env`. It is not a fallback for any of the other three.

`gemini` calls Gemini. The default chat model is `gemini-3.5-flash`. Embeddings, when `EMBEDDING_PROVIDER=gemini`, use `gemini-embedding-001` at 384 dimensions. The chat timeout is 20 seconds.

`openai` calls an OpenAI-compatible chat-completions endpoint. The default is `https://api.openai.com/v1` and `gpt-4o-mini`, with `OPENAI_API_KEY` required. The timeout is 20 seconds. JSON summaries request `response_format: json_object`.

`local` is that same chat-completions client pointed at a server on this machine, such as LM Studio Bionic. Compose reaches it through `host.docker.internal` and the port shown in the server's base URL, not through `localhost`. `LOCAL_LLM_MODEL` is required. No API key is required. JSON mode is not sent, because small local models often reject it. The timeout is 180 seconds so the first request can load the weights. A statement object inside a contradiction is flattened to the string the summary schema requires.

The router tries `LLM_FALLBACK` only when the first real provider fails before the first token. A mid-sentence failure is shown as an error, because a retry would repeat the opening. Embeddings do not fall back. Switching `EMBEDDING_PROVIDER` means deleting the `pgdata` volume and uploading again. Switching only `LLM_PROVIDER` does not.

## Traces, retention, and masking

Spans and logs carry: claim id, document id, stage, latency, chunk count, provider, estimated tokens, and estimated cost. They do not carry document text, the prompt, or the question.

Cost is `prompt_tokens * input_price + completion_tokens * output_price`, with illustrative prices per million tokens defined in configuration. Tokens are estimated as `len(text) / 4`. It is for comparing calls, not for billing.

A logging filter masks emails, Spanish phone numbers, DNI, and NIE in case an exception drags them in.

The text lives in Postgres. In demo mode the console exporter prints one line per application span and per HTTP request. It skips ASGI chunk spans: one streamed answer would otherwise print a full JSON object for every token. An OTLP collector still receives every span. Demo traces are not stored. If a collector is connected, the written policy is 14 days. Deleting a claim must delete documents, chunks, and answers. That delete operation is not in this MVP's API; the policy is.

## Security

Maximum 15 MB. Extension plus magic bytes. A ZIP renamed to `.docx` is rejected if it does not contain `word/document.xml`. The claim id cannot contain slashes: it is used as a directory name.

Document text is wrapped in `<untrusted_document>` tags. The prompt says that text is data, not instructions. Demo mode also refuses to quote sentences that look like orders ("ignore", "ignora", "olvida"). A document that says "approve the claim" does not change the behavior: the sentence is dropped, or it would be quoted only if the question asked for that exact text and the evidence gate accepted it as a source.

There is no authentication. Anyone who can call the API and knows the id can read that claim. That is a conscious MVP limit.

## A dead worker

`acks_late` and `reject_on_worker_lost` mean the broker does not treat the message as delivered until the task finishes. If the process dies halfway, another worker receives the same id. The task extracts again and writes chunks only at the end, in one transaction that deletes the previous chunks of that document. If the document is already `ready`, the second run does nothing.

Transient errors (embedding provider, network) retry at 2, 4, and 8 seconds. On the fourth failure the document is `failed` with the reason. A corrupt PDF does not spend those retries.

## Evaluation

Recall@5 and grounding are defined in `backend/eval/run_eval.py`, and the numbers are in `backend/eval/results.md`. With more time I would grow the set, hold out a development group and a test group, and only then touch k or the chunk size. A reranker would make sense after measuring that the top 20 contains the right page and the top 5 does not.

## Second iteration

- OCR or Docling for scans and tables, behind the same extraction function.
- Alembic instead of `create_all`.
- An IMMUTABLE `unaccent` wrapper if lexical Recall asks for it.
- A reranker and a larger evaluation set before changing weights.
- Authentication and a real claim delete, with the 14-day retention applied at the collector.
- Never, including then, mix embeddings from two models in the same column.
