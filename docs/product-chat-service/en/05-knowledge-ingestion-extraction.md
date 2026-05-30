---
created: 2026-05-17
updated: 2026-05-30
status: active
topics:
  - knowledge-base
  - ingestion
  - extraction
  - graphrag
related_code:
  - my_agents/api/knowledge_bases.py
  - my_agents/api/documents.py
  - my_agents/knowledge/models.py
  - my_agents/knowledge/extraction.py
  - my_agents/knowledge/pdf_uploads.py
  - my_agents/knowledge/uploads.py
  - tests/test_knowledge_ingestion.py
---

# Knowledge ingestion and deterministic extraction

This note explains the text-based V1 knowledge-ingestion slice.

## What is implemented now

A user can create a personal or group knowledge base, attach either a JSON text document or a supported text-based file upload, and run deterministic ingestion over the stored document text. The upload path accepts text-based PDFs, Markdown files, and plain-text files. The existing bodyless synchronous `/documents/{document_id}/ingest` contract remains unchanged. The additive `/documents/{document_id}/ingest/async` endpoint returns a queued run for polling-based multi-file UX; local/default mode can process it in-process, while hosted demos can use `MY_AGENTS_INGESTION_EXECUTION_MODE=external_worker` and a separate `python -m my_agents.ingestion_worker` process so parser/indexing work does not starve the web service.

The ingestion pass creates:

- chunks;
- JSON-backed embeddings through a provider boundary;
- extraction run records with status/stage/progress fields;
- entities;
- entity mentions;
- co-occurrence relationships between adjacent extracted entities;
- provenance back to document chunks;
- upload metadata on documents (`source_filename`, content type, byte size, SHA-256, page count when available, parser name);
- page provenance on chunks through `source_page` when the source document is a PDF.

## Ingestion flow

```mermaid
flowchart TD
    Upload[POST /documents/upload] --> Dispatch{Upload type}
    Dispatch -->|.pdf application/pdf| Parser[pypdf_text_v2]
    Dispatch -->|.md/.markdown text/markdown| Markdown[utf8_markdown_v1]
    Dispatch -->|.txt text/plain| Plain[utf8_text_v1]
    Parser -->|low or empty text| Fallback[deterministic_stream_fallback_v1 for simple legacy fixtures]
    Parser -->|enough text| Metadata[Document source metadata]
    Parser -->|enough text| Content[Stored page-separated text]
    Fallback --> Metadata
    Fallback --> Content
    Markdown --> Metadata
    Markdown --> Content
    Plain --> Metadata
    Plain --> Content
    Text[POST /documents JSON text] --> Content
    Content --> RunChoice{Ingestion endpoint}
    RunChoice -->|sync compatibility| Run[POST /documents/{id}/ingest]
    RunChoice -->|async queued| Async[POST /documents/{id}/ingest/async]
    Async --> Mode{Execution mode}
    Mode -->|local in_process| Background[threaded local worker]
    Mode -->|hosted external_worker| Worker[python -m my_agents.ingestion_worker]
    Background --> Poll[GET /documents/{id}/extraction-runs/{run_id}]
    Worker --> Poll
    Run --> Chunks[DocumentChunk rows]
    Poll --> Chunks
    Chunks --> Page[PDF source_page when available]
    Chunks --> Provider{Embedding mode}
    Provider -->|deterministic default| Embeddings[32-d lexical-hash embedding_json]
    Provider -->|openai opt-in| OpenAI[langchain-openai JSON embedding_json]
    Embeddings -->|Postgres migrations| Pgvector[embedding_vector pgvector column]
    OpenAI -->|Postgres migrations| Pgvector
    Chunks --> Entities[Entity extraction]
    Entities --> Mentions[EntityMention provenance]
    Mentions --> Relationships[EntityRelationship co_occurs_with]
```

## Async ingestion progress contract

The async slice is additive and intentionally demo-shaped, with a web/worker split available for hosted demos:

- `POST /documents/{document_id}/ingest/async` requires the same ingest permission as the sync endpoint and returns `202 Accepted`;
- response body is an `ExtractionRunResponse` with `status=pending`, `stage=queued`, and `progress_percent=0`;
- execution uses a fresh SQLAlchemy session and updates the same run through `running` stages (`chunking`, `embedding`, optional `indexing`, `entities`) to `completed`; local/default mode does this in-process, while `external_worker` mode leaves the run queued until `python -m my_agents.ingestion_worker` claims it;
- `GET /documents/{document_id}/extraction-runs/{run_id}` requires document read access and returns the latest progress/counts;
- failures persist `status=failed`, `stage=failed`, and a bounded display-safe `error`;
- the backend still uses database polling rather than Redis/Celery/durable queue semantics, so production worker supervision and stale-run recovery remain follow-up work.

Response shape:

```json
{
  "id": "run-id",
  "document_id": "document-id",
  "status": "pending|running|completed|failed",
  "stage": "queued|chunking|embedding|indexing|entities|completed|failed",
  "progress_percent": 0,
  "chunk_count": 0,
  "entity_count": 0,
  "relationship_count": 0,
  "error": null,
  "started_at": null,
  "completed_at": null
}
```

## Why deterministic extraction

The project must keep tests offline and credential-free. The backend now uses `pypdf`
for normal text-based PDF extraction, keeps a deterministic stream fallback for simple
fixtures, and decodes Markdown/plain text uploads as UTF-8 text without adding a heavy
parser dependency. Chunking and entity extraction remain deterministic, while embeddings
now use a provider boundary: deterministic lexical-hash vectors by default, or OpenAI
embeddings through `langchain-openai` when `MY_AGENTS_EMBEDDING_MODE=openai`. SQLite and
offline tests keep JSON embeddings only; Postgres stores the same vectors in a pgvector
column after Alembic migrations so retrieval can use SQL vector search.

This is a scaffold for review-visible architecture, not a claim of production extraction quality.

## Parallel ingestion concurrency lesson

During multi-file UX testing against Postgres, a user uploaded and ingested three files concurrently and hit `psycopg.errors.DeadlockDetected`. The failure came from extraction, not retrieval: parallel runs extracted overlapping entity names and the older select-then-insert helper raced on the unique `entities.name` constraint.

The ingestion service now pre-collects entity names for a run, inserts them in a stable sorted order, and uses dialect-aware conflict-safe inserts (`ON CONFLICT DO NOTHING` for Postgres/SQLite) before creating mentions and relationships. This preserves the frontend's parallel upload/ingestion goal while avoiding shared canonical-entity lock cycles. Regression coverage lives in `tests/test_knowledge_ingestion.py::test_parallel_async_ingest_shared_entities_complete`. A learner-focused incident note is in [`docs/learning/06-parallel-ingestion-postgres-deadlock.md`](../../learning/06-parallel-ingestion-postgres-deadlock.md).

## Current limitations

- PDF support is text-first through `pypdf`, with a legacy literal/FlateDecode stream fallback for simple PDFs;
- Markdown/plain-text support is UTF-8 text-only; Markdown structure is not parsed into a typed AST;
- no scanned PDF OCR, docx, HTML, or CSV/JSON structural ingestion yet;
- no cloud object storage adapter yet;
- OpenAI embeddings are opt-in and require `OPENAI_API_KEY`; OpenAI extraction calls are not implemented yet;
- async ingestion supports local in-process threads and hosted external-worker mode, but no durable Redis/Celery-style queue or production worker supervisor yet;
- pgvector acceleration is exact SQL vector search over authorized candidates; ANN/vector indexes, production cross-encoder packaging, latency budgets, and retrieval-quality evals remain future work.

Thin permission-aware RAG and graph expansion now live in the next learning note.

## Testing evidence

`tests/test_knowledge_ingestion.py` verifies:

- personal KB creation;
- group KB membership enforcement;
- document attachment to a KB;
- text-path regression for bodyless ingestion;
- accepted PDF upload metadata persistence;
- accepted Markdown/plain-text upload metadata persistence and retrieval;
- pgvector schema migration coverage with SQLite fallback;
- rejected unsupported or unsafe upload input;
- PDF parser/page provenance on chunks;
- a local skip-if-missing regression for the LangChain Academy LangGraph PDF that previously produced only boilerplate text;
- ingestion creates chunks, entities, relationships, and extraction-run summaries;
- async ingestion returns a queued run, supports direct polling, persists completed/failed progress, and respects document permissions;
- parallel async ingestion of documents with shared entity names completes without select-then-insert entity deadlocks;
- outsiders cannot create group KBs for groups they do not belong to.

## Revision history

- 2026-05-30: Updated async ingestion docs for the hosted external-worker execution mode and clarified remaining durable-queue gaps.
- 2026-05-22: Documented and fixed the Postgres parallel-ingestion entity deadlock lesson.
- 2026-05-22: Added additive async ingestion progress endpoints and extraction-run status fields for multi-file upload UX.
- 2026-05-21: Extended `/documents/upload` to accept Markdown and plain-text UTF-8 files while preserving PDF parsing and provenance.
- 2026-05-21: Added pgvector chunk storage for Postgres retrieval acceleration while keeping JSON/SQLite fallback.
- 2026-05-21: Upgraded PDF extraction to `pypdf_text_v2`, added legacy fallback documentation, 32-d lexical-hash embedding fixtures, and local LangGraph PDF regression coverage.
- 2026-05-21: Added embedding provider boundary with deterministic default and optional OpenAI JSON-backed embeddings via `langchain-openai`.
- 2026-05-20: Updated for strict V1 Phase 2 PDF upload, metadata persistence, and chunk page provenance.
- 2026-05-17: Updated limitations after adding thin permission-aware RAG in the next slice.
- 2026-05-17: Created after adding thin end-to-end knowledge-base ingestion and deterministic extraction.
