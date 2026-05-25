# ContextForge changelog

## 2026-05-25 — Add generated document metadata profile retrieval

- **Why:** Chunk-only vector search can miss documents when the user asks by purpose, domain, alias, or another language and the important meaning is distributed across a large PDF.
- **Behavior / contract impact:** Ingestion now stores one search-oriented metadata profile per extraction run with generated title, description, summary, keywords, topics, entities, language, generator/model/prompt provenance, and a profile embedding. Candidate Scouts search those profiles as `document_metadata_profile` source evidence, but still inject original chunks so citations remain grounded in source text.
- **Verification evidence:** Added regression coverage proving a document can be retrieved by metadata-only oncology terms absent from its body text, plus migration/settings coverage for the new table and knobs.

## 2026-05-25 — Retrieve documents by uploaded filename metadata

- **Why:** Users may refer to an uploaded file by its visible filename/title, while that filename often does not appear inside the extracted PDF/text body.
- **Behavior / contract impact:** Authorized document `title` and `source_filename` metadata now participate in candidate gathering as `document_metadata` source evidence. Filename-like references also bypass the ambiguous “this document” clarification branch so the backend can try the matching authorized document first.
- **Verification evidence:** Added regression coverage for `NCT06159946_Prot_000` resolving to the matching uploaded file even though the body text lacks that identifier.

## 2026-05-25 — Make clarification human-in-the-loop

- **Why:** Ambiguous document-scope prompts can come in any user language, so the backend should not hard-code an English clarification sentence.
- **Behavior / contract impact:** `clarification_required` runs now complete without invoking the assistant graph and return `reply: ""` plus a structured `clarification` payload (`message_key`, `reason_code`, `input_slot`, route/scope metadata). The client/human-in-the-loop layer localizes the prompt and collects the missing document reference.
- **Verification evidence:** Added sync and streaming conversation API coverage for the structured clarification payload and graph-bypass behavior.

## 2026-05-24 — Add optional cross-encoder reranking

- **Why:** Retrieval quality is one of the service's critical paths, and the referenced RAG reranking guidance recommends a two-stage pipeline: fast first-stage retrieval followed by cross-encoder scoring over a bounded top-k set.
- **Behavior / contract impact:** `reranking.py` now exposes a `Reranker` interface, keeps deterministic reranking as the default, and adds an optional `CrossEncoderReranker` behind `MY_AGENTS_RERANKER_MODE=cross_encoder`. The cross-encoder only sees candidates already authorized and gathered by `RetrievalService`, and the event evidence now records the active reranker.
- **Debug impact:** Added opt-in Rich print traces for ContextForge role handoffs when `MY_AGENTS_DEBUG_KNOWLEDGE_CONTEXT_LOGGING=true`, showing which role sends which message/payload to the next role.
- **Verification evidence:** Added `tests/test_context_forge_reranking.py` to prove deterministic ordering, fake cross-encoder reordering, service-level bounded reranker handoff, and env-driven settings without requiring the optional runtime dependency.

## 2026-05-24 — Introduce dedicated retrieval-agent service boundary

- **Why:** Retrieval quality became a first-class product concern. The previous conversation run path called routing and `RetrievalService` directly, which made query planning, source policy, candidate fusion, context packing, and retrieval evidence feel like scattered tuning work rather than an explicit RAG layer.
- **Behavior / contract impact:** Added `ContextForgeService` as the production-surface retrieval orchestration boundary under `my_agents/agents/context_forge/`. The package implements deterministic role classes for query planning, source-boundary handoff, candidate gathering, fusion, reranking seam, high-recall context packing, and redacted retrieval evidence. Hard authorization and SQL/database retrieval remain in `my_agents/knowledge/` service-layer code.
- **Structured retrieval impact:** Ingestion now stores structured knowledge entities for API endpoints, config keys, shell commands, error codes, and database table references. Enumeration prompts such as “list API endpoints in this document” can retrieve by extracted entity type with chunk/page/offset provenance.
- **Verification evidence:** Added `tests/test_context_forge_contracts.py` and `tests/test_context_forge_structured_retrieval.py`. Targeted verification passed with ContextForge tests, migration tests, RAG permission tests, conversation/streaming tests, and Ruff/format checks during the Ralph implementation session.
