---
created: 2026-06-25
updated: 2026-06-25
status: active
topics:
  - ingestion
  - performance
  - pdf
  - embeddings
  - metadata
  - observability
related_code:
  - scripts/measure_ingestion_performance.py
  - my_agents/knowledge/timing.py
  - my_agents/knowledge/uploads.py
  - my_agents/knowledge/pdf_uploads.py
  - my_agents/knowledge/extraction.py
  - my_agents/settings.py
  - tests/test_knowledge_ingestion.py
---

# Ingestion performance log

[한국어 요약](../ko/ingestion-performance-log.md) | English

This is the living ledger for document-ingestion performance work. It records measured
before/after timings, the behavior-preserving changes applied, quality guards, and lessons
learned. Use the repo-local `$performance-optimizer` workflow for future optimization work
and keep this log updated when ingestion latency is measured again.

Do not paste raw document text, filenames that should stay private, document IDs, chunk IDs,
emails, tokens, API keys, or secrets. Use parser names, source metadata, counts, redacted
phase names, and millisecond values.

## Measurement surfaces

### Repeatable local harness

Use the synthetic benchmark harness for deterministic before/after code-path comparisons:

```bash
uv run python scripts/measure_ingestion_performance.py \
  --scenario pdf \
  --repeat 3 \
  --repeat-units 80 \
  --output /tmp/my-agents-ingestion-pdf.json
```

The harness creates an isolated SQLite database, forces deterministic embeddings and metadata,
runs the real parser/ingestion/retrieval-smoke path, and emits redacted JSON.

### Real local API timing panel

Use the Rich timing panel when profiling a real document through the API:

```bash
MY_AGENTS_DEBUG_INGESTION_TIMING_LOGGING=true uv run fastapi dev main.py
```

The panel prints redacted upload and extraction spans. It does not print raw filenames or
document text.

## Phase taxonomy

| Phase | Meaning | Notes |
| --- | --- | --- |
| `upload.read` | Read multipart upload bytes. | Should be tiny for current file limits. |
| `parse.pdf.validate` | Filename, content-type, size, and PDF magic-byte checks. | Safety gate. |
| `parse.pdf.sha256` | Source checksum calculation. | Stored as source metadata. |
| `parse.pdf.parser.pymupdf_text_v1` | Primary native-text PDF extraction. | Happy path for text PDFs. |
| `parse.pdf.quality_gate.*` | Validation of extracted text quality. | Protects retrieval from garbage text. |
| `parse.pdf.classify` | Lazy pypdf classification for fallback routing. | Runs only after PyMuPDF fails quality. |
| `document.persist` | Persist parsed document/source metadata/artifacts. | Upload-path DB write. |
| `chunking` | Split stored document text into retrieval chunks. | Should stay low relative to network calls. |
| `embedding.chunks` | Embed chunk text. | OpenAI-backed mode is network-bound. |
| `entities.extract` / `entities.upsert` | Deterministic entity extraction and canonical entity insert/fetch. | Quality/count-sensitive. |
| `indexing.persist_chunks` | Persist chunks, mentions, relationships, structured facts, and vectors. | DB-bound. |
| `metadata.generate` | Generate document-level search metadata. | OpenAI-backed mode is network/model-bound. |
| `metadata.embed` / `metadata.persist` | Embed and persist metadata profile. | Supports filename/topic/profile retrieval. |

`metadata.generate` may run in parallel with chunk embedding/indexing. Treat timing rows as
spans that can overlap, not as additive wall-clock components.

## INGEST-PERF-2026-06-25-A: Aliro PDF optimization run

### Scenario

- Source: Aliro 1.0 specification PDF supplied locally by the owner.
- Parser accepted: `pymupdf_text_v1`.
- Source size: 3,569,429 bytes.
- Pages: 195.
- Extracted characters: 409,701.
- Runtime shape: OpenAI embeddings, OpenAI metadata generation.
- Embedding batch-size experiment: `MY_AGENTS_EMBEDDING_BATCH_SIZE=64`.
- Goal: reduce ingestion wall-clock time without changing accepted parser output or derived
  artifact counts.

### Baseline profile

| Area | Time |
| --- | ---: |
| Upload total | 8.50s |
| `parse.pdf` | 8.47s |
| Extraction total | 27.66s |
| `embedding.chunks` | 11.27s |
| `metadata.generate` | 12.52s |
| `indexing.persist_chunks` | 2.60s |
| End-to-end | 36.16s |

Quality/count signature:

| Field | Value |
| --- | ---: |
| `page_count` | 195 |
| `content_chars` | 409701 |
| `chunk_count` | 392 |
| `entity_count` | 1935 |
| `relationship_count` | 6537 |
| `structured_entity_count` | 127 |

### Optimization 1: embedding batch-size tuning

Changed deployment/runtime config for the experiment from the default batch size to:

```bash
MY_AGENTS_EMBEDDING_BATCH_SIZE=64
```

This does not change embedding model, chunk text, parser output, or retrieval artifacts. It
reduces the number of OpenAI embedding requests for 392 chunks.

| Area | Before | After | Change |
| --- | ---: | ---: | ---: |
| Extraction total | 27.66s | 23.43s | -4.23s / -15.3% |
| `embedding.chunks` | 11.27s | 6.59s | -4.68s / -41.5% |
| End-to-end | 36.16s | 31.88s | -4.28s / -11.8% |

Quality/count signature stayed unchanged.

### Optimization 2: parallel OpenAI metadata generation

Changed `KnowledgeExtractionService.ingest_document` so OpenAI-backed metadata generation starts
after chunking, then overlaps with chunk embedding, entity extraction/upsert, and chunk/index
persistence. Only the network/model metadata-generation call runs in the background. SQLAlchemy DB
writes stay on the main ingestion thread.

| Area | Before | After | Change |
| --- | ---: | ---: | ---: |
| Extraction total | 23.43s | 13.01s | -10.42s / -44.5% |
| End-to-end | 31.88s | 21.50s | -10.38s / -32.6% |

The timing panel showed `metadata_generation=parallel`; `metadata.generate` still took about
12.54s as a span, but it overlapped the rest of extraction.

Quality/count signature stayed unchanged.

### Diagnostic step: PDF parser sub-timing

Added redacted PDF subphase timing under the existing ingestion timing panel. The next real run
showed:

| Phase | Time |
| --- | ---: |
| `parse.pdf.classify` | 3.29s |
| `parse.pdf.parser.pymupdf_text_v1` | 1.76s |
| `parse.pdf.quality_gate.pymupdf_text_v1` | 0.26s |
| `parse.pdf` total | 8.39s |

Finding: pypdf classification was the largest visible upload parse cost on the native-text happy
path, and it duplicated work before PyMuPDF succeeded.

### Optimization 3: lazy PDF classification

Changed PDF parsing so the happy path runs:

```text
validate -> sha256 -> PyMuPDF parser -> PyMuPDF quality gate -> accept
```

If PyMuPDF fails, returns empty text, or fails the quality gate, the parser then runs pypdf
classification and routes through pypdf, Docling, Tesseract, and legacy fallbacks as before.
The existing quality gate still protects accepted text.

| Area | Before | After | Change |
| --- | ---: | ---: | ---: |
| Upload total | 8.41s | 5.00s | -3.41s / -40.6% |
| `parse.pdf` | 8.39s | 4.97s | -3.41s / -40.7% |
| `parse.pdf.classify` | 3.29s | skipped | removed from happy path |
| Extraction total | 11.97s | 11.57s | roughly stable |
| End-to-end | 20.39s | 16.57s | -3.82s / -18.7% |

Final quality/count signature stayed unchanged:

| Field | Value |
| --- | ---: |
| `parser` | `pymupdf_text_v1` |
| `page_count` | 195 |
| `content_chars` | 409701 |
| `chunk_count` | 392 |
| `entity_count` | 1935 |
| `relationship_count` | 6537 |
| `structured_entity_count` | 127 |

### End-state comparison

| Stage | Upload total | Extraction total | End-to-end | Main change |
| --- | ---: | ---: | ---: | --- |
| Baseline | 8.50s | 27.66s | 36.16s | Serial OpenAI metadata/embedding; PDF pre-classification. |
| Batch-size tuning | 8.45s | 23.43s | 31.88s | Fewer embedding requests. |
| Parallel metadata | 8.48s | 13.01s | 21.50s | Metadata generation overlapped extraction/indexing. |
| Lazy classification | 5.00s | 11.57s | 16.57s | Happy-path PDFs skip pypdf pre-classification. |

Final end-to-end improvement: about 54% versus the baseline for this local Aliro PDF profile.

## Lessons learned

1. **Measure before optimizing.** The first guess was hosting/RAM pressure, but the largest
   extraction costs were OpenAI/network/model spans.
2. **Separate repeatable harnesses from real local profiles.** The benchmark harness is good for
   behavior-preserving code-path comparisons; the Rich panel is better for real document latency.
3. **Phase rows can overlap.** After metadata parallelization, `metadata.generate` remained large
   but no longer dominated wall-clock extraction time.
4. **Avoid quality tradeoffs first.** The winning changes did not reduce chunk count, disable
   metadata, remove quality gates, or drop fallback parsers.
5. **Lazy work beats deleted work.** pypdf classification still exists for fallback routing, but it
   no longer taxes native-text PDFs where PyMuPDF passes the quality gate.
6. **Batch-size changes are runtime-dependent.** Batch size 64 was a clear win in this run; larger
   values should be treated as an environment-specific experiment because provider limits and
   concurrent ingestions can change the result.

## Remaining bottlenecks

- `metadata.generate` remains the largest individual span, but it now overlaps extraction work.
  Further optimization would involve model/input/metadata-quality tradeoffs and should be measured
  carefully.
- `parse.pdf` still has unattributed overhead beyond the visible subphases on the large PDF. Do not
  optimize further without another timing pass.
- Hosted production performance still depends on OpenAI latency, DB latency, and worker/web process
  isolation. A hosted ingestion-worker smoke remains operationally useful.
