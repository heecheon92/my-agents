---
created: 2026-05-17
updated: 2026-06-07
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
    Upload["POST /documents/upload"] --> Dispatch{"Upload type"}
    Dispatch -->|".pdf application/pdf"| PdfGate["PDF validation + classification"]
    Dispatch -->|".md/.markdown text/markdown"| Markdown["utf8_markdown_v1"]
    Dispatch -->|".txt text/plain"| Plain["utf8_text_v1"]

    PdfGate --> PyMuPDF["pymupdf_text_v1 primary parser"]
    PyMuPDF --> PyMuPDFGate{"Valid extracted text?"}
    PyMuPDFGate -->|"yes"| Metadata["Document source metadata"]
    PyMuPDFGate -->|"no"| Pypdf["pypdf_text_v2 text-PDF compatibility"]
    Pypdf --> PypdfGate{"Valid extracted text?"}
    PypdfGate -->|"yes"| Metadata
    PypdfGate -->|"no"| Docling["docling_markdown_v1 structured fallback"]
    Docling --> DoclingGate{"Valid extracted text?"}
    DoclingGate -->|"yes"| Metadata
    DoclingGate -->|"no"| Tesseract["tesseract_ocr_v1 small-PDF OCR fallback"]
    Tesseract --> TesseractGate{"Valid extracted text?"}
    TesseractGate -->|"yes"| Metadata
    TesseractGate -->|"no"| Legacy["deterministic_stream_fallback_v1 simple fixtures"]
    Legacy --> LegacyGate{"Valid extracted text?"}
    LegacyGate -->|"yes"| Metadata
    LegacyGate -->|"no"| Reject["Reject upload with safe error"]

    Metadata --> Content["Stored normalized text"]
    Markdown --> Metadata
    Plain --> Metadata
    Text["POST /documents JSON text"] --> Content
    Content --> RunChoice{"Ingestion endpoint"}
    RunChoice -->|"sync compatibility"| Run["POST /documents/{id}/ingest"]
    RunChoice -->|"async queued"| Async["POST /documents/{id}/ingest/async"]
    Async --> Mode{"Execution mode"}
    Mode -->|"local in_process"| Background["threaded local worker"]
    Mode -->|"hosted external_worker"| Worker["python -m my_agents.ingestion_worker"]
    Background --> Poll["GET /documents/{id}/extraction-runs/{run_id}"]
    Worker --> Poll
    Run --> Chunks["DocumentChunk rows"]
    Poll --> Chunks
    Chunks --> Page["PDF source_page when available"]
    Chunks --> Provider{"Embedding mode"}
    Provider -->|"deterministic default"| Embeddings["32-d lexical-hash embedding_json"]
    Provider -->|"openai opt-in"| OpenAI["langchain-openai JSON embedding_json"]
    Embeddings -->|"Postgres migrations"| Pgvector["embedding_vector pgvector column"]
    OpenAI -->|"Postgres migrations"| Pgvector
    Chunks --> Entities["Entity extraction"]
    Entities --> Mentions["EntityMention provenance"]
    Mentions --> Relationships["EntityRelationship co_occurs_with"]
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

The project must keep tests offline and credential-free. The backend now uses
`pymupdf_text_v1` as the primary fast local PDF text extractor, then falls back through
`pypdf_text_v2`, `docling_markdown_v1`, constrained `tesseract_ocr_v1`, and finally the
legacy deterministic stream parser for simple fixtures. Markdown/plain-text uploads are
decoded as UTF-8 text without adding a heavy parser dependency. Chunking and entity
extraction remain deterministic, while embeddings use a provider boundary: deterministic
lexical-hash vectors by default, or OpenAI embeddings through `langchain-openai` when
`MY_AGENTS_EMBEDDING_MODE=openai`. SQLite and offline tests keep JSON embeddings only;
Postgres stores the same vectors in a pgvector column after Alembic migrations so
retrieval can use SQL vector search.

This is a scaffold for review-visible architecture, not a claim of production extraction quality.

## Why the current custom chunker exists

The current chunker in `my_agents/knowledge/extraction.py` is not a claim that a custom
splitter is universally better than LangChain's `RecursiveCharacterTextSplitter` or other
LangChain splitters. It is better for this service's current V1 constraints because it
returns exactly the persistence shape the backend needs: `(content, start_offset,
end_offset, source_page)`. That tuple is written directly to `DocumentChunkModel`, powers
page-aware citations, and keeps re-ingestion deterministic across SQLite, Postgres, and
offline tests.

Key differences from dropping in `RecursiveCharacterTextSplitter` directly:

- **Page provenance is first-class.** PDF text is split by `PDF_PAGE_SEPARATOR` first, then
  chunked per page, so a chunk does not accidentally cross page boundaries. `source_page`
  can be stored without reconstructing it from metadata later.
- **Offsets are first-class.** The service stores `start_offset` and `end_offset` against
  the original normalized document text. Those offsets are useful for auditability,
  citation context, and future extraction/debug views. LangChain splitters can carry
  metadata, but this project would still need an adapter to recover stable offsets.
- **The chunk size is intentionally document-sized.** `_CHUNK_TARGET_CHARS=1500` and
  `_CHUNK_OVERLAP_CHARS=200` keep chunks large enough for answer context while only using
  overlap when a long sentence/paragraph must be fixed-width split. Short paragraphs and
  sentences remain intact instead of being fragmented just because a separator hierarchy
  matched.
- **It is deterministic and dependency-thin.** Ingestion tests can verify exact chunk
  boundaries, ordinals, offsets, page numbers, entity mentions, and embeddings without
  depending on LangChain splitter behavior or version-specific separator defaults.
- **It matches the current retrieval/citation contract.** The backend stores chunks,
  embeddings, entity mentions, relationships, and metadata profiles in SQL models. A
  generic splitter would still need service-specific glue for page labels, offsets,
  idempotent re-ingestion, and test fixtures.

Use a LangChain splitter later if the product needs token-aware splitting, language-aware
code splitting, Markdown/HTML structure preservation, or a standard loader/splitter
pipeline. For now, the custom chunker is a small persistence/provenance adapter, not a
full document-understanding strategy.

## Parallel ingestion concurrency lesson

During multi-file UX testing against Postgres, a user uploaded and ingested three files concurrently and hit `psycopg.errors.DeadlockDetected`. The failure came from extraction, not retrieval: parallel runs extracted overlapping entity names and the older select-then-insert helper raced on the unique `entities.name` constraint.

The ingestion service now pre-collects entity names for a run, inserts them in a stable sorted order, and uses dialect-aware conflict-safe inserts (`ON CONFLICT DO NOTHING` for Postgres/SQLite) before creating mentions and relationships. This preserves the frontend's parallel upload/ingestion goal while avoiding shared canonical-entity lock cycles. Regression coverage lives in `tests/test_knowledge_ingestion.py::test_parallel_async_ingest_shared_entities_complete`. A learner-focused incident note is in [`docs/learning/06-parallel-ingestion-postgres-deadlock.md`](../../learning/06-parallel-ingestion-postgres-deadlock.md).

## Current limitations

- PDF support is local-first through `pymupdf_text_v1`, with `pypdf_text_v2`, Docling Markdown, constrained Tesseract OCR, and legacy literal/FlateDecode fallbacks;
- Markdown/plain-text support is UTF-8 text-only; Markdown structure is not parsed into a typed AST;
- scanned-PDF support is only a small local OCR fallback for PDFs within the page cap; there is no production-grade OCR/layout pipeline yet;
- no docx, HTML, or CSV/JSON structural ingestion yet;
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
- PDF parser fallback ordering, including PyMuPDF, Docling, constrained Tesseract, and legacy rejection cases;
- PDF parser/page provenance on chunks;
- custom chunk target/overlap behavior;
- a local skip-if-missing regression for the LangChain Academy LangGraph PDF that previously produced only boilerplate text;
- ingestion creates chunks, entities, relationships, and extraction-run summaries;
- async ingestion returns a queued run, supports direct polling, persists completed/failed progress, and respects document permissions;
- parallel async ingestion of documents with shared entity names completes without select-then-insert entity deadlocks;
- outsiders cannot create group KBs for groups they do not belong to.

## Revision history

- 2026-06-07: Updated parser docs for the PyMuPDF-primary PDF pipeline, Docling/Tesseract fallbacks, Mermaid rendering, and the custom chunker rationale.
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
