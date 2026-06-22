# ContextForge changelog

## 2026-06-22 — Reduce first-stage retrieval scan fan-out

- **Why:** Local timing showed `candidate_gather` was dominated by five repeated authorized chunk scans and thousands of per-chunk entity mention queries, while Postgres vector search itself was fast.
- **Behavior / contract impact:** Candidate gathering now embeds the rewritten query once per retrieval attempt, uses document-level authorization rows for metadata matching, loads chunks only for matched documents in metadata/profile/overview lanes, and batches graph-expansion entity lookup instead of querying each chunk. Authorization filters and retrieval response contracts are unchanged.
- **Verification evidence:** `tests/test_permission_aware_rag.py` covers metadata/profile/overview retrieval and batched graph expansion; `tests/test_context_forge_reranking.py` covers the timing panel path.

## 2026-06-22 — Add nested candidate-gather timing rows

- **Why:** The first local timing panel showed `candidate_gather` consumed most retrieval latency, but that top-level row still hid whether the cost came from embeddings, SQL/vector search, JSON fallback, metadata profile matching, entity expansion, or overview supplementation.
- **Behavior / contract impact:** The local `MY_AGENTS_DEBUG_RETRIEVAL_TIMING_LOGGING=true` Rich panel now forwards existing retrieval and embedding spans during candidate gathering as redacted `candidate_gather.*` rows. Repeated nested spans aggregate their call count and total elapsed milliseconds. Metrics labels and API responses are unchanged.
- **Verification evidence:** `tests/test_context_forge_reranking.py` covers nested `candidate_gather.*` Rich timing rows and prompt redaction.

## 2026-06-22 — Add local retrieval timing Rich panel

- **Why:** Aggregate Prometheus histograms showed retrieval was slow but did not give a local, per-run answer to which ContextForge phase dominated one conversation turn.
- **Behavior / contract impact:** `MY_AGENTS_DEBUG_RETRIEVAL_TIMING_LOGGING=true` now prints a human-readable Rich timing panel for each ContextForge retrieval attempt. The panel records redacted phase timings and counts for authorization count, query planning, candidate gather, candidate fusion, reranking, and context packing without printing raw prompts or document text. API response shape and metrics labels are unchanged.
- **Verification evidence:** `tests/test_context_forge_reranking.py` covers the Rich timing output and redaction; `tests/test_settings.py` covers the env flag.

## 2026-06-22 — Lazy-load cross-encoder reranker weights

- **Why:** Web-intended conversation runs can pass through graph-owned RAG setup before provider-hosted web search. Eagerly initializing the optional cross-encoder made those turns pay a local Hugging Face cold-start cost even when document candidate reranking was skipped.
- **Behavior / contract impact:** `CrossEncoderReranker` still represents `MY_AGENTS_RERANKER_MODE=cross_encoder`, but it loads the underlying `sentence-transformers` model only on the first non-empty rerank call. Empty/no-retrieval routes still report the configured reranker name without loading model weights.
- **Verification evidence:** `tests/test_context_forge_reranking.py` covers lazy construction, no-load empty rerank, and first-load candidate scoring.

## 2026-06-16 — Re-scope ContextForge behind the public RAG Agent boundary

- **Why:** The assistant-facing retrieval surface should be the RAG Agent, while ContextForge remains the permission-first retrieval implementation that can evolve internally.
- **Behavior / contract impact:** Conversation run graph context now provides a `SqlAlchemyRagAgentRuntime`; the general assistant invokes RAG retrieval in-graph, and that runtime delegates to `invoke_context_forge_graph(...)`. ContextForge docs now describe it as the delegated engine rather than the public assistant entrypoint.
- **Verification evidence:** `uv run pytest -q tests/test_conversations_api.py tests/test_permission_aware_rag.py` covered graph-owned retrieval, safe halt paths, and permission filtering through the delegated ContextForge path.

## 2026-06-14 — Expand metadata-profile hits into body evidence

- **Why:** Generated metadata profiles can correctly identify the right document while the injected candidate is only the title/header chunk, leaving buried source facts such as project creator or stack details unavailable to the assistant.
- **Behavior / contract impact:** `document_metadata_profile` remains a document-locator source, but matched profiles now expand to the strongest body/source chunks from the same authorized document. Existing semantic, metadata, and graph-expansion source identity is preserved for duplicate chunks, and KB/source authorization is unchanged.
- **Verification evidence:** Added regression coverage for metadata-profile retrieval injecting body chunks instead of only a heading, plus targeted ContextForge/RAG verification and full backend tests.

## 2026-06-11 — Make reranker top-k runtime configurable

- **Why:** Production deployments need a small, explicit latency/memory knob for the second-stage reranker candidate window without changing authorization or retrieval code.
- **Behavior / contract impact:** `MY_AGENTS_RERANKER_TOP_K` now controls the authorized candidate count sent to deterministic or cross-encoder reranking. The default remains `40`.
- **Verification evidence:** Added settings and ContextForge service coverage for the env override.

## 2026-06-10 — Add thin LangGraph RetrievalGraph wrapper

- **Why:** Future agents should be able to call knowledge-base retrieval as a typed graph/tool capability when they need more evidence, but the product must keep hard authorization and retrieval SQL inside existing service boundaries.
- **Behavior / contract impact:** `graph.py` now exposes a real ContextForge LangGraph wrapper (`retrieve_context -> retry_required_evidence? -> assess_evidence`) and `invoke_context_forge_graph(...)`. Conversation run retrieval goes through this graph without changing API response shape. The graph returns the underlying `ContextForgeResult`, bounded attempt count, and insufficient-evidence flag. Documentation now treats this graph state as runtime-only and warns future agents not to checkpoint or persist raw retrieval graph state.
- **Verification evidence:** Added graph contract tests for required-evidence retry and no-retrieval skip behavior; targeted ContextForge/RAG tests passed.

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
