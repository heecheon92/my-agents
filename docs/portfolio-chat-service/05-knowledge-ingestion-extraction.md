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
  - tests/test_knowledge_ingestion.py
---

# Knowledge ingestion and deterministic extraction

This note explains the PDF-first V1 knowledge-ingestion slice.

## What is implemented now

A user can create a personal or group knowledge base, attach either a JSON text document or a text-based PDF upload, and run deterministic ingestion over the stored document text. The PDF upload path is additive: the existing bodyless `/documents/{document_id}/ingest` contract remains unchanged.

The ingestion pass creates:

- chunks;
- deterministic embedding fixtures;
- extraction run records;
- entities;
- entity mentions;
- co-occurrence relationships between adjacent extracted entities;
- provenance back to document chunks;
- PDF upload metadata on documents (`source_filename`, content type, byte size, SHA-256, page count, parser name);
- page provenance on chunks through `source_page` when the source document is a PDF.

## Ingestion flow

```mermaid
flowchart TD
    Upload[POST /documents/upload PDF] --> Parser[pypdf_text_v2]
    Parser -->|low or empty text| Fallback[deterministic_stream_fallback_v1 for simple legacy fixtures]
    Parser -->|enough text| Metadata[Document source metadata]
    Parser -->|enough text| Content[Stored page-separated text]
    Fallback --> Metadata
    Fallback --> Content
    Text[POST /documents JSON text] --> Content
    Content --> Run[POST /documents/{id}/ingest]
    Run --> Chunks[DocumentChunk rows]
    Chunks --> Page[PDF source_page when available]
    Chunks --> Embeddings[32-d deterministic lexical-hash embedding_json]
    Chunks --> Entities[Entity extraction]
    Entities --> Mentions[EntityMention provenance]
    Mentions --> Relationships[EntityRelationship co_occurs_with]
```

## Why deterministic extraction

The project must keep tests offline and credential-free. The backend now uses `pypdf`
for normal text-based PDF extraction and keeps a deterministic stream fallback for simple
fixtures. Chunking, entity extraction, and embedding fixtures remain deterministic so the
service can prove the data lifecycle before adding provider-backed embeddings or OCR.

This is a scaffold for portfolio-visible architecture, not a claim of production extraction quality.

## Current limitations

- PDF support is text-first through `pypdf`, with a legacy literal/FlateDecode stream fallback for simple PDFs;
- no scanned PDF OCR, docx, or HTML ingestion yet;
- no cloud object storage adapter yet;
- no OpenAI embedding/extraction provider call yet;
- no production vector similarity ranking yet.

Thin permission-aware RAG and graph expansion now live in the next learning note.

## Testing evidence

`tests/test_knowledge_ingestion.py` verifies:

- personal KB creation;
- group KB membership enforcement;
- document attachment to a KB;
- text-path regression for bodyless ingestion;
- accepted PDF upload metadata persistence;
- rejected unsupported or unsafe upload input;
- PDF parser/page provenance on chunks;
- a local skip-if-missing regression for the LangChain Academy LangGraph PDF that previously produced only boilerplate text;
- ingestion creates chunks, entities, relationships, and extraction-run summaries;
- outsiders cannot create group KBs for groups they do not belong to.

## Revision history

- 2026-05-21: Upgraded PDF extraction to `pypdf_text_v2`, added legacy fallback documentation, 32-d lexical-hash embedding fixtures, and local LangGraph PDF regression coverage.
- 2026-05-20: Updated for strict V1 Phase 2 PDF upload, metadata persistence, and chunk page provenance.
- 2026-05-17: Updated limitations after adding thin permission-aware RAG in the next slice.
- 2026-05-17: Created after adding thin end-to-end knowledge-base ingestion and deterministic extraction.
