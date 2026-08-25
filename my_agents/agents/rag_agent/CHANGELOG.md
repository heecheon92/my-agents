# RAG Agent changelog

## 2026-08-25 — Add document-level citation display metadata

- Why: chunk-level provenance remains useful for persistence and audit, but rendering every chunk and internal ID overloads users.
- Behavior/contract impact: `CitationResponse` now includes nullable `document_title` and `knowledge_base_name` while retaining chunk fields for backward-compatible audit. The intended product presentation groups evidence by `document_id`, shows one document name plus knowledge-base name and optional unique pages, and hides document/KB/chunk IDs and snippets from ordinary citation details.
- Verification: sync completion and refresh-safe run detail assert both names, and the generated OpenAPI contract covers both nullable fields.

## 2026-08-25 — Separate consulted context from answer-supported citations

- Why: the previous pipeline persisted every positive-score chunk admitted to answer composition as a citation, so the user-facing provenance panel overstated which sources visibly supported the generated answer.
- Behavior/contract impact: new runs persist all consulted chunks with nullable `used_in_answer` attribution and a run-level attribution version. `ConversationRunResponse.citations` now contains only the conservative post-hoc answer-supported subset, while nullable `consulted_sources` exposes the complete user-visible consulted superset using the same persisted IDs. Legacy runs preserve their flat citations and return `consulted_sources=null`; attributed zero-source or zero-match runs return `[]`.
- Verification: selector tests cover support, conservative no-match, generic-language rejection, and distinctive IDs. Sync, SSE, resume-stream, replay, and refresh-safe run-detail tests cover field parity and identical overlap IDs.

## 2026-08-25 — Preserve bounded provenance for many-chunk documents

- Why: complete Markdown documents with more than 100 small chunks were authorized, current, and fully readable, but the citation safety cap replaced every chunk with an empty list and incorrectly triggered insufficient evidence.
- Behavior/contract impact: full-document reads now validate up to 2,000 overlapping chunks and retain at most 100 evenly distributed provenance chunks, including the first and last. The public citation ceiling remains bounded while documents such as the 190-chunk regression can complete instead of being treated as unavailable. The deterministic planner fallback also recognizes Korean `모두` when it appears with a document reference and read/summary task, matching the live Luna decision for the reported prompt.
- Verification: `tests/test_full_document_retrieval.py` covers a 190-section Markdown document at the service and conversation-run boundaries, including complete coverage, 100 distributed citations, and absence of the insufficient-evidence fallback.

## 2026-08-24 — Add Luna-backed retrieval-tool selection

- Why: literal comprehensive-intent phrases missed natural multilingual requests such as a named document plus “빠짐없이 검토,” while the RAG Agent had no model-backed planning step of its own.
- Behavior/contract impact: after broad private-knowledge delegation, fixed `gpt-5.6-luna` standard/low tool selection chooses exactly one typed operation: `search_authorized_chunks` or `read_authorized_document_comprehensively`. Deterministic mode, invalid tool output, and provider failure fall back to a local composed-intent rule. Luna never selects trusted IDs, authorizes access, changes budgets, reads raw full-document text, or composes the final answer.
- Verification: offline fake-model tests cover model/effort policy, strict required tool binding, Korean comprehensive intent, focused controls, invalid output, provider timeout, and deterministic false-positive prevention. A credentialed smoke selected the comprehensive tool for the original Korean regression prompt.

## 2026-08-24 — Add typed full-document target and range seams

- Why: focused hybrid retrieval can under-cover distant sections when the user explicitly asks for complete-document review.
- Behavior/contract impact: `RagAgentRuntime`/`SqlAlchemyRagAgentRuntime` now resolve one authorized user-selectable document and return bounded normalized extracted-text ranges with half-open offsets, an internal decimal cursor, complete/partial state, and overlapping citation chunks. The default full-read threshold is 24,000 characters; larger files currently expose only the first 12,000-character range. Ambient system and hidden staging documents are excluded, and every read revalidates current permission and selected-KB scope.
- Persistence/API impact: raw text stays node-local and out of checkpoints/events/traces. Conversation contracts expose only compact `document_coverage` and redacted `full_document_read` metadata; the internal cursor is not public.
- Verification: covered by `tests/test_full_document_retrieval.py` and settings validation. Automatic multi-range synthesis and token-aware budgeting remain follow-up work.

## 2026-08-12 — Separate ambient system context from visible provenance

- Why: system knowledge should influence answers without appearing as a user-visible document source.
- Behavior/contract impact: prompt context for ambient system chunks retains only snippet text, internal citation rows remain auditable, and public run/detail/event/citation contracts omit system provenance and ambient counts.
- Verification: covered by system-knowledge contract tests, responder prompt tests, OpenAPI contract tests, and conversation API tests.

## 2026-08-09 — Freeze the display-safe trace contract

- Why: the frontend needs a stable localized activity timeline without guessing event/stage fields or rendering arbitrary stored JSON.
- Behavior/contract impact: `agent_trace` now has closed stage/event/status vocabularies and an allowlisted evidence schema; the persisted-event serializer recursively filters uncontracted fields.
- Verification: covered by `tests/test_agent_event_contract.py`, `tests/test_api_error_contract.py`, and conversation API tests.

## 2026-06-16 — Promote RAG Agent to the assistant retrieval boundary

- Why: `general_assistant` should be able to decide inside its own graph whether to retrieve document evidence, while keeping ContextForge as the permission-first implementation detail.
- Behavior/contract impact: added a public `RagAgentRuntime`/`SqlAlchemyRagAgentRuntime` retrieval seam and `RagAgentRetrievalResult`; `general_assistant` now invokes the RAG Agent before memory/answer nodes, and the public retrieval-agent name is `RAG Agent` while `ContextForge` is recorded as the internal delegated implementation.
- Verification: covered by `tests/test_rag_agent_contracts.py`, `tests/test_conversations_api.py`, and `tests/test_permission_aware_rag.py`.

## 2026-06-07 — Add dedicated graph form

- Why: the RAG Agent should be graph-shaped when it is a concrete agent contract, but the graph must not absorb retrieval, authorization, ingestion, persistence, or provider execution.
- Behavior/contract impact: added `graph.py` with a deterministic LangGraph `plan_workflow -> verify_workflow` path and routed compact trace generation through the verified graph output.
- Verification: covered by `tests/test_rag_agent_contracts.py`.

## 2026-06-06 — Add deterministic RAG Agent workflow contract

- Why: the V1 agentic RAG story needs a named concrete `rag_agent` surface while preserving ContextForge as the existing Retrieval Agent boundary.
- Behavior/contract impact: added deterministic stage planning and verification for compact localized traces; no retrieval, authorization, or provider execution moved into this package.
- Verification: covered by `tests/test_rag_agent_contracts.py` and conversation API trace tests.
