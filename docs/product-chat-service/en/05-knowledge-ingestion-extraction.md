---
created: 2026-05-17
updated: 2026-06-23
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

A user can create a personal or group knowledge base, attach either a JSON text document or a supported file upload, and run deterministic ingestion over the stored document text. The upload path accepts text-based PDFs, Markdown files, plain-text files, `.xlsx`, `.pptx`, and `.docx`. DOCX support is DOCX-only: legacy binary `.doc` remains unsupported. The existing bodyless synchronous `/documents/{document_id}/ingest` contract remains unchanged. The additive `/documents/{document_id}/ingest/async` endpoint returns a queued run for polling-based multi-file UX; local/default mode can process it in-process, while hosted demos can use `MY_AGENTS_INGESTION_EXECUTION_MODE=external_worker` and a separate `python -m my_agents.ingestion_worker` process so parser/indexing work does not starve the web service.

The ingestion pass creates:

- chunks;
- JSON-backed embeddings through a provider boundary;
- extraction run records with status/stage/progress fields;
- entities;
- entity mentions;
- co-occurrence relationships between adjacent extracted entities;
- provenance back to document chunks;
- upload metadata on documents (`source_filename`, content type, byte size, SHA-256, page count when available, parser name);
- Markdown-first parse artifacts for supported Office uploads, including DOCX block elements with Markdown offsets;
- page provenance on chunks through `source_page` when the source document is a PDF, and parser-derived `source_location_json` when upload artifacts provide stable offsets.

## Ingestion flow

```mermaid
flowchart TD
    Upload["POST /documents/upload"] --> Dispatch{"Upload type"}
    Dispatch -->|".pdf application/pdf"| PdfGate["PDF validation"]
    Dispatch -->|".md/.markdown text/markdown"| Markdown["utf8_markdown_v1"]
    Dispatch -->|".txt text/plain"| Plain["utf8_text_v1"]
    Dispatch -->|".xlsx/.pptx/.docx OOXML"| Office["Office parser to Markdown + elements"]

    PdfGate --> PyMuPDF["pymupdf_text_v1 primary parser"]
    PyMuPDF --> PyMuPDFGate{"Valid extracted text?"}
    PyMuPDFGate -->|"yes"| Metadata["Document source metadata"]
    PyMuPDFGate -->|"no"| Classify["Lazy pypdf classification for fallback routing"]
    Classify --> Pypdf{"Native or mixed text?"}
    Pypdf -->|"yes"| PypdfParser["pypdf_text_v2 text-PDF compatibility"]
    Pypdf -->|"no"| Docling["docling_markdown_v1 structured fallback"]
    PypdfParser --> PypdfGate{"Valid extracted text?"}
    PypdfGate -->|"yes"| Metadata
    PypdfGate -->|"no"| Docling
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
    Office --> Metadata
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
- frontend multi-file upload fan-out is a safe backend runtime hint exposed through `/health` as `frontend_config.documents.upload_concurrency`, backed by `MY_AGENTS_DOCUMENT_UPLOAD_CONCURRENCY` (default `3`);
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

Office uploads keep the same deterministic/local-first boundary. XLSX uses
`openpyxl_markdown_v1`, PPTX uses `python_pptx_markdown_v1`, and DOCX uses
`docling_docx_markdown_v1`. DOCX parsing produces canonical Markdown plus
provider-neutral block elements such as `word_heading`, `word_paragraph`, and
`word_table`; the Markdown is mirrored into `documents.content` for the current chunker,
while the parse artifact preserves offsets and heading paths for future citation,
rendering, and Upstage-normalized provider output.

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
  can be stored without reconstructing it from metadata later. Line-heavy PDF pages are
  still packed into retrieval-sized chunks within the same page, so extracted line breaks do
  not create hundreds of tiny embeddings when the page text can safely travel together.
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

## Ingestion performance measurement

Use the benchmark harness for repeatable before/after ingestion measurements:

```bash
uv run python scripts/measure_ingestion_performance.py \
  --scenario pdf \
  --repeat 3 \
  --repeat-units 80 \
  --output /tmp/my-agents-ingestion-pdf.json
```

The harness creates an isolated SQLite database, forces deterministic embedding and
metadata modes, uploads a synthetic text/Markdown/PDF fixture, runs the real ingestion
service, performs a small retrieval smoke, and emits redacted JSON. The output tracks
parse, persist, ingest, retrieval-smoke, total wall time, RSS delta, parser/source
metadata, artifact counts, and a quality signature. Use the same scenario before and
after an optimization; parser/source changes, missing metadata profiles, missing retrieval
hits, or unexpected entity loss should block the optimization unless the quality contract
is explicitly being revised.

For live local diagnosis while using the API, enable the ingestion timing panel instead:

```bash
MY_AGENTS_DEBUG_INGESTION_TIMING_LOGGING=true uv run fastapi dev main.py
```

This prints redacted Rich tables for upload parsing and extraction/indexing runs. It is
useful when a real local document is slow and you need to identify whether the bottleneck
is upload file read, PDF validation/checksum/classification, a specific PDF parser attempt,
PDF quality gating, DB persistence, stale artifact cleanup, chunking, chunk embedding,
entity upsert/linking, chunk/index persistence, metadata generation, metadata embedding, or
final commit. It prints counts and source metadata only; raw filenames and document text
stay out of the panel. When OpenAI metadata generation is active, metadata generation starts
after chunking and runs in parallel with chunk embedding/indexing. Treat the phase rows as
spans that can overlap rather than additive wall-clock components.

PDF classification is lazy on the happy path: the upload parser first tries
`pymupdf_text_v1` and accepts it if the existing quality gate passes. If PyMuPDF fails,
returns empty text, or fails the quality gate, the parser then runs pypdf classification
for encrypted/corrupted/native/mixed/no-text fallback routing before trying pypdf,
Docling, Tesseract, and legacy fallbacks.

### Measured optimization snapshot

The 2026-06-25 local optimization run used a 195-page Aliro 1.0 specification PDF
(`pymupdf_text_v1`, 3.57 MB, 409,701 extracted characters) with OpenAI embeddings,
OpenAI metadata generation, and `MY_AGENTS_EMBEDDING_BATCH_SIZE=64`. The benchmark goal
was to reduce wall-clock time without changing parser/source output or ingestion quality
counts.

| Step | Upload total | Extraction total | End-to-end | Main change |
| --- | ---: | ---: | ---: | --- |
| Baseline | 8.50s | 27.66s | 36.16s | OpenAI embeddings and metadata ran serially; PDF pre-classified before PyMuPDF. |
| Batch-size tuning | 8.45s | 23.43s | 31.88s | Larger embedding batch reduced chunk embedding latency. |
| Parallel metadata | 8.48s | 13.01s | 21.50s | OpenAI metadata generation overlapped chunk embedding/indexing. |
| Lazy classification | 5.00s | 11.57s | 16.57s | Happy-path native-text PDFs skip pypdf pre-classification. |

Quality guards stayed stable across the run: `page_count=195`, `content_chars=409701`,
`chunk_count=392`, `entity_count=1935`, `relationship_count=6537`, and
`structured_entity_count=127`. The final end-to-end improvement was about 54% versus the
baseline while keeping the same accepted parser and derived artifact counts. Treat these
numbers as one local profile, not a global SLA; OpenAI latency and PDF shape will vary.

## Parallel ingestion concurrency lesson

During multi-file UX testing against Postgres, a user uploaded and ingested three files concurrently and hit `psycopg.errors.DeadlockDetected`. The failure came from extraction, not retrieval: parallel runs extracted overlapping entity names and the older select-then-insert helper raced on the unique `entities.name` constraint.

The ingestion service now pre-collects entity names for a run, inserts them in a stable sorted order, and uses dialect-aware conflict-safe inserts (`ON CONFLICT DO NOTHING` for Postgres/SQLite) before creating mentions and relationships. This preserves the frontend's parallel upload/ingestion goal while avoiding shared canonical-entity lock cycles. Regression coverage lives in `tests/test_knowledge_ingestion.py::test_parallel_async_ingest_shared_entities_complete`. A learner-focused incident note is in [`docs/learning/06-parallel-ingestion-postgres-deadlock.md`](../../learning/06-parallel-ingestion-postgres-deadlock.md).

## Current limitations

- PDF support is local-first through `pymupdf_text_v1`, with `pypdf_text_v2`, Docling Markdown, constrained Tesseract OCR, and legacy literal/FlateDecode fallbacks;
- Markdown/plain-text support is UTF-8 text-only; Markdown structure is not parsed into a typed AST;
- DOCX support is local DOCX-only parsing through Docling Markdown and block elements; legacy binary `.doc` is still unsupported;
- scanned-PDF support is only a small local OCR fallback for PDFs within the page cap; there is no production-grade OCR/layout pipeline yet;
- no HTML or CSV/JSON structural ingestion yet;
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
- accepted Office upload metadata, Markdown parse artifacts, and DOCX source-location citations;
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

- 2026-06-23: Added DOCX-only upload support through local Docling Markdown/block artifacts while keeping legacy `.doc` unsupported.
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
