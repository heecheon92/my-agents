---
created: 2026-06-24
updated: 2026-06-24
status: active
topics:
  - rag
  - docx
  - chunking
  - retrieval-debugging
related_code:
  - my_agents/knowledge/extraction.py
  - tests/test_knowledge_ingestion.py
---

# DOCX Markdown chunking must keep headings with body context

## Symptom

DOCX upload appeared to succeed: the file was accepted, Docling produced Markdown, ingestion created chunks, and citations existed. But retrieval quality was poor for natural questions about the uploaded manual. Queries such as `ACCU-BANK 개요` tended to retrieve short table-of-contents or heading fragments instead of the paragraph that actually explained ACCU-BANK.

The misleading part was that this was not a parser failure. The Markdown representation contained the answer-bearing text.

## Confirming evidence

A local reproduction with the uploaded AxACU DOCX showed:

- extracted Markdown length: about 23,852 characters;
- old ingestion output: 473 chunks;
- early chunks were tiny fragments such as title lines, `<!-- image -->`, section labels, and TOC entries;
- the answer paragraph existed, but it was isolated from the heading/section context.

That chunk shape made retrieval favor short lexical matches and headings instead of useful answer context.

## Root cause

The generic chunker split text into semantic units on blank-line boundaries and emitted every unit under the target size as its own chunk.

That behavior is acceptable for plain text and simple PDF page text, but Docling DOCX Markdown often inserts blank lines between almost every structural item:

```md
**CHAPTER 1. INTRODUCTION**

- 1.1. **개요**

ACCU-BANK는 출입 통제 시스템에 사용되는 사용자 인증용 주 장치로서...

<!-- image -->
```

The generic chunker treated those as separate retrieval chunks. The resulting chunks were structurally valid but too small to be useful for RAG.

## Fix

DOCX now uses a Word-Markdown-specific chunking path inside `my_agents/knowledge/extraction.py`:

```text
_chunk_document_text(document)
├─ pdf            -> _chunk_pdf_text() -> _chunk_text() per page
├─ word_document  -> _chunk_word_markdown_text()
└─ other text     -> _chunk_text()
```

The DOCX path still reuses the existing semantic-unit helpers, but changes the final packing behavior:

1. split Docling Markdown into semantic units;
2. remove standalone `<!-- image -->` placeholders;
3. coalesce adjacent units into roughly 1,500-character chunks;
4. keep offsets so parser-derived source-location provenance still works;
5. preserve page/text behavior for non-DOCX sources.

After the fix, the same sample DOCX produced 16 retrieval-sized chunks instead of 473 tiny chunks.

## Why this fixed retrieval

RAG retrieval needs answer-bearing chunks, not just syntactically clean Markdown blocks. The fixed DOCX chunk shape keeps nearby headings, section labels, tables, and paragraphs together so the retriever and answer generator see enough context to answer.

Before:

```text
- 1.1. 개요
```

or:

```text
ACCU-BANK는 출입 통제 시스템에 사용되는...
```

After:

```md
**CHAPTER 1. INTRODUCTION**

- 1.1. **개요**

ACCU-BANK는 출입 통제 시스템에 사용되는 사용자 인증용 주 장치로서...
```

## Rejected fixes

- **Replace Docling immediately.** Rejected because the Markdown contained the needed text; the bug was chunk granularity.
- **Route DOCX through PDF chunking unchanged.** Rejected because PDF chunking is page-oriented and still emits generic blank-line chunks inside each page.
- **Use Upstage as the immediate fix.** Upstage remains promising for charts/images, but this failure was fixable locally without adding a cloud dependency or changing the current architecture.
- **Only improve prompt wording.** Rejected because prompts cannot recover body context that was not retrieved or injected.

## Operational lesson

Changing chunking code affects future ingestion only. Already-uploaded documents keep their old chunks until they are re-ingested or uploaded again.

Also, local Uvicorn auto-reload may not always make this kind of backend behavior change obvious immediately. When validating ingestion changes, fully restart the backend process and then re-ingest the document before judging retrieval quality.

## Verification

The fix added regression coverage that checks DOCX Markdown headings stay with body context and standalone image placeholders are not emitted as their own chunks.

Validation after the fix:

```bash
uv run pytest -q
uv run pytest tests/test_office_uploads.py tests/test_knowledge_ingestion.py -q
uv run ruff check . --no-cache
uv run ruff format --check .
```

The sample AxACU DOCX also parsed to about 23,852 Markdown characters and 16 retrieval chunks.

## Future follow-up

This is still a pragmatic Markdown coalescer, not a full document-structure AST. If the project later supports richer document types, image/table reasoning, or Upstage parser outputs, the next abstraction should probably be a structured document representation that can emit Markdown, retrieval text, citations, and LLM context from the same source.

## Revision history

- 2026-06-24: Created learning log for `DOCX Markdown chunking must keep headings with body context`.
