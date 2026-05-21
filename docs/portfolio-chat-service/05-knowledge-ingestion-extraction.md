---
created: 2026-05-17
updated: 2026-05-21
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

A user can create a personal or group knowledge base, attach either a JSON text document or a supported text-based file upload, and run deterministic ingestion over the stored document text. The upload path accepts text-based PDFs, Markdown files, and plain-text files. The existing bodyless `/documents/{document_id}/ingest` contract remains unchanged.

The ingestion pass creates:

- chunks;
- JSON-backed embeddings through a provider boundary;
- extraction run records;
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
    Content --> Run[POST /documents/{id}/ingest]
    Run --> Chunks[DocumentChunk rows]
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

## Why deterministic extraction

The project must keep tests offline and credential-free. The backend now uses `pypdf`
for normal text-based PDF extraction, keeps a deterministic stream fallback for simple
fixtures, and decodes Markdown/plain text uploads as UTF-8 text without adding a heavy
parser dependency. Chunking and entity extraction remain deterministic, while embeddings
now use a provider boundary: deterministic lexical-hash vectors by default, or OpenAI
embeddings through `langchain-openai` when `MY_AGENTS_EMBEDDING_MODE=openai`. SQLite and
offline tests keep JSON embeddings only; Postgres stores the same vectors in a pgvector
column after Alembic migrations so retrieval can use SQL vector search.

This is a scaffold for portfolio-visible architecture, not a claim of production extraction quality.

## Current limitations

- PDF support is text-first through `pypdf`, with a legacy literal/FlateDecode stream fallback for simple PDFs;
- Markdown/plain-text support is UTF-8 text-only; Markdown structure is not parsed into a typed AST;
- no scanned PDF OCR, docx, HTML, or CSV/JSON structural ingestion yet;
- no cloud object storage adapter yet;
- OpenAI embeddings are opt-in and require `OPENAI_API_KEY`; OpenAI extraction calls are not implemented yet;
- pgvector acceleration is exact SQL vector search over authorized candidates; ANN/vector indexes and cross-encoder reranking are future work.

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
- outsiders cannot create group KBs for groups they do not belong to.

## Revision history

- 2026-05-21: Extended `/documents/upload` to accept Markdown and plain-text UTF-8 files while preserving PDF parsing and provenance.
- 2026-05-21: Added pgvector chunk storage for Postgres retrieval acceleration while keeping JSON/SQLite fallback.
- 2026-05-21: Upgraded PDF extraction to `pypdf_text_v2`, added legacy fallback documentation, 32-d lexical-hash embedding fixtures, and local LangGraph PDF regression coverage.
- 2026-05-21: Added embedding provider boundary with deterministic default and optional OpenAI JSON-backed embeddings via `langchain-openai`.
- 2026-05-20: Updated for strict V1 Phase 2 PDF upload, metadata persistence, and chunk page provenance.
- 2026-05-17: Updated limitations after adding thin permission-aware RAG in the next slice.
- 2026-05-17: Created after adding thin end-to-end knowledge-base ingestion and deterministic extraction.
