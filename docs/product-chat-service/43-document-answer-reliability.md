# Document-answer reliability

This milestone hardens existing retrieval and answer composition. It adds no worker service,
provider, dependency, frontend setting, or public API field. Hosted rollout is separate from local
verification; current evidence belongs in [implementation tracking](../implementation-tracking.md).

## Comparable embedding spaces

Migration `20261017_0041` adds an immutable `embedding_indexes` registry and nullable
`embedding_index_id` references on document chunks and generated document metadata profiles.
The identity is a SHA-256 digest of these canonical fields:

| Field | Meaning |
| --- | --- |
| provider | Embedding adapter, not the answer or metadata-generation provider |
| model | Exact embedding model identifier |
| dimensions | Actual validated response length, checked against an explicit configured dimension |
| encoding | The embedding boundary's tokenizer/encoding identity |
| preprocessing | Versioned text/vector processing semantics, including length-safe averaging |
| version | Application-controlled index identity version |

New ingestion validates vectors and records identities in the same transaction as their rows.
The generated metadata model remains separately recorded; it is not the profile's embedding model.
Changing any identity field creates a different space. Equal vector dimensions alone never make
spaces compatible. Invalid/non-finite vectors cannot be registered.

All vector-scoring paths check identity: ordinary JSON ranking, selected-document ranking,
metadata-profile ranking, and PostgreSQL distance queries. PostgreSQL filters identity and vector
dimension before ranking and guards the distance expression with `CASE`. Existing document/KB/user
permission filters remain mandatory. Missing or incompatible identities have no vector score;
authorized keyword/BM25, filename/metadata, and structured retrieval lanes remain available.

## Migration and existing documents

1. Apply `uv run alembic upgrade head` against the intended database before starting this code.
2. Migration preserves every old vector but leaves its identity **NULL**. It does not infer an
   embedding model from current settings, vector size, or the metadata-generation model.
3. To restore semantic ranking for legacy documents, explicitly re-ingest them under the intended
   embedding configuration using the existing authorized `POST /documents/{id}/ingest` or
   `POST /knowledge-bases/{kb_id}/documents/{id}/ingest` operation. This recomputes chunk and profile
   embeddings and their identities using the existing ingestion lifecycle.
4. A model/preprocessing change requires the same explicit re-index decision. Never relabel old
   vectors with the newly configured model's identity. There is no automatic provider-paid backfill.

An operator can inspect legacy coverage without exposing document content:

```sql
SELECT 'chunks' AS kind, count(*) AS unknown_count
FROM document_chunks WHERE embedding_index_id IS NULL
UNION ALL
SELECT 'metadata_profiles', count(*)
FROM document_metadata_profiles WHERE embedding_index_id IS NULL;
```

The registry contains configuration only; account/conversation/document erasure still deletes its
existing content-bearing rows. Unreferenced registry records contain no user text or identity.
Downgrade removes the new identity columns/registry and preserves old vector payloads; an older
application then loses these compatibility protections. Do not downgrade while the new app is running.

## Evidence windows

Jev keeps the same query, synthetic candidate ID, authorized excerpt, and truncation-flag contract.
A bounded lexical selector now chooses a contiguous query-bearing region instead of always taking
an initial UTF-8 prefix. It favors rare query terms, bounds evaluated anchors, preserves the exact
source substring, and retains prefix selection when no useful term matches. Byte and serialized
request limits are unchanged. This is a selection heuristic, not exhaustive document coverage or
knowledge of Jev's private tokenizer. Source chunks and citation identifiers are never rewritten.

The optional cross-encoder uses its own loaded tokenizer and maximum sequence length. Query and
special-token space are reserved first; overlapping document-token windows are scored and their
maximum score becomes the candidate score. Every decoded pair is re-counted before prediction.
At most 16 windows per candidate are allowed. If a query/window cannot fit, the tokenizer cannot
be inspected, or scores are invalid, the complete fused order is preserved rather than silently
scoring a clipped prefix. Retrieval diagnostics report `cross_encoder_fallback_deterministic` for
that fallback, with request-local state. Its actual model download/runtime latency remains an opt-in deployment
check; the normal Jev path does not load it.

## Final answer text budgets

Ordinary answer composition counts the selected/pinned answer model; the document-workspace
adapter independently counts its workspace model. Known model mappings use a verified local
tokenizer cache. Unknown model names, unavailable/corrupt cache files, or unsupported encodings
use conservative UTF-8 byte estimates. Request-time cache misses do not trigger downloads or
silently substitute another model's tokenizer.

The final rendered-text budget includes the incomplete-context disclosure added after reducing
optional context. The current question is preserved; if mandatory text cannot fit, the request
fails explicitly. Existing retrieval character-budget behavior and preliminary continuity selection remain unchanged;
the final rendered-text boundary enforces its model-aware estimate.
Internal delivery metadata records the model, tokenizer/counting method, estimated text count,
truncation, and `rendered_text_only` scope. It does not claim exact provider token usage or include
native image/file/tool processing in the text estimate.

## Evidence and limits

`tests/fixtures/retrieval_reliability.json` contains synthetic English, Korean, and code passages,
with relevant facts near the end and a weaker leading candidate. Before the change, all three
facts were absent from the Jev excerpt. Afterward all three are present under the same 3,000-byte
limit. A mocked scorer verifies the resulting rank change; it is not a live model-quality evaluation.

A local 100-iteration selector-only probe measured about 0.95 ms English, 0.41 ms Korean, and
0.62 ms code per excerpt (previous prefix slicing: 0.001–0.010 ms). These figures exclude provider,
database, and full-request latency and are not production performance claims.

Tests also cover identity/version mismatches, legacy fallback, permission isolation, real PostgreSQL
vector-distance safety, migration preservation/rollback, tokenizer-window bounds, unknown-model
fallback, and final prompt disclosure accounting. The generated OpenAPI is unchanged. Broader
held-out/live Jev relevance and optional cross-encoder quality/latency measurements remain separate.
