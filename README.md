# RAG assistant for claims analysis

MVP for Norte Seguros (a fictional company). An adjuster uploads policy PDFs and DOCX letters for a claim, the system processes them in the background, and the adjuster can ask questions in natural language. The answer streams in, with citations to the chunk and the page, plus a structured summary validated with Pydantic. When the documents are not enough, the assistant says it cannot answer.

The default mode needs no API keys: the model and the embeddings are deterministic.

The interview guide is [`docs/interview-prep.pdf`](docs/interview-prep.pdf). The decisions the brief asks for are in [`DECISIONS.md`](DECISIONS.md). The diagram is in [`docs/architecture.md`](docs/architecture.md). Questions to try with the sample files are in [`samples/QUESTIONS.md`](samples/QUESTIONS.md).

## What is done

- Upload of one or more PDF/DOCX files. The response is `202` with a job id and the initial status. It does not wait for processing.
- Celery + Redis pipeline: extraction, chunking with page and section, embeddings, and PostgreSQL with pgvector. The task is idempotent, retries with backoff, and publishes each document's status.
- Hybrid search (vector + English full text) combined with Reciprocal Rank Fusion, always filtered by claim.
- Questions over SSE, with real citations. An explicit refusal when the evidence is weak.
- Language providers behind one interface: fake, Gemini, and an OpenAI-compatible endpoint. A 20 second timeout, and fallback only between real providers, only if the first one has not emitted text.
- Pydantic summary. If it does not validate, one repair attempt and, if that also fails, an explicit error.
- React: upload, a list that shows errors, streaming chat, a clickable citation, and a useful / not useful vote.
- A set of 12 questions, a Recall@5 and grounding script, pytest (including real PostgreSQL via Docker), and chat tests.
- OpenTelemetry, JSON logs with a trace id, and masking of email, phone, and national-id numbers.
- Docker Compose: API, worker, Redis, PostgreSQL/pgvector, and frontend. CI with Ruff, pytest, TypeScript, and Vitest.

## What is not done

- No OCR. A scanned PDF fails with the reason "No extractable text. Scanned PDFs are not supported in this version."
- No login. Isolation is by claim id. The brief does not ask for users.
- Docling is not the default extractor. The reason is in `DECISIONS.md`.
- The cost on a trace is an estimate from `len(text) / 4` and a price table, not a provider invoice.
- No reranker and no RRF weight tuning on a large corpus.

## Run demo mode

Docker is required.

```bash
docker compose up --build
```

- App: http://localhost:8080
- API: http://localhost:8000/api/health

Health should report `llm_provider: fake` and `embedding_provider: fake`. The example claim in the UI is `SIN-1001`. Upload a PDF that contains real text (not a scan) and ask once the document says "ready".

To use Gemini instead of the fake provider, export `GEMINI_API_KEY` and set `LLM_PROVIDER` and `EMBEDDING_PROVIDER` to `gemini` in `docker-compose.yml`. Chat uses `gemini-3.8-flash` (`gemini-2.0-flash` was shut down on 1 June 2026). Embeddings stay on `gemini-embedding-001`, which accepts 384 dimensions. Do not set the fake provider as the fallback of a real one: the vector spaces cannot be mixed. If you change the embedding model, delete the `pgdata` volume and reindex.

## Tests

Unit tests do not need a database. The integration test starts `pgvector/pgvector:pg16` itself through testcontainers. Docker must be running; if it is not, that test is skipped locally and fails in CI.

```bash
cd backend
pip install -r requirements.txt
ruff check app tests eval
pytest
```

```bash
cd frontend
npm ci
npm test
npm run lint
```

## Evaluation

Twelve questions about a synthetic home policy and an endorsement that changes the water-damage limit. Recall@5 checks that the expected page is among the five fused chunks. Grounding checks that the answer cites that source and contains the expected text. The two questions with no evidence (jewelry, surveyor) must be refusals.

```bash
cd backend
python -m eval.build_fixtures
python -m eval.run_eval
```

Without `DATABASE_URL`, the script starts a Postgres with pgvector through Docker and writes `backend/eval/results.md`. Inside Compose:

```bash
docker compose exec api python -m eval.run_eval
```

## Layout

```
backend/app          API, worker, retrieval, providers
backend/tests        unit tests and the pgvector integration test
backend/eval         corpus, dataset, and metrics
frontend             React + TypeScript
docs                 architecture and interview preparation
samples              example policy, endorsement, and question guide
docker-compose.yml
```

Uploaded files live on a volume shared by the API and the worker. The worker cannot read a file that exists only inside the API container.
