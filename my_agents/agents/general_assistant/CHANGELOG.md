# general_assistant CHANGELOG

This changelog records why the production-surface `general_assistant` agent folder needed
meaningful behavior, graph-state, provider, or documentation changes.

## 2026-08-26 — Stream checkpoint resume instead of replaying a buffered answer

- **Why:** `/resume/stream` called the synchronous resume endpoint to completion before its first SSE yield, so the frontend kept the document-choice card frozen and showed no live progress until the entire answer existed.
- **Behavior/contract impact:** sync and streamed resume share one authorization/atomic-claim helper. Streamed resume emits `run_resumed` first, then drives the checkpoint through LangGraph `messages` and `updates`, exposing retrieval/graph progress and real answer deltas with cooperative cancellation checks.
- **Verification:** the checkpointed document-selection API regression now requires `run_resumed` first and `retrieval_completed`/`graph_invoked` before `answer_delta`; graph streaming, persistence, and conversation API suites cover the shared boundary.

## 2026-08-24 — Delegate retrieval-operation choice to the RAG Agent

- **Why:** `general_assistant` should own broad source delegation and final response composition, while focused-versus-comprehensive retrieval belongs to the RAG Agent.
- **Behavior/contract impact:** after the source gate selects private knowledge, the graph consumes the RAG Agent's Luna/deterministic typed tool decision and routes to focused ContextForge retrieval or the bounded comprehensive-document branch. Only compact tool name/reason metadata enters graph state; authorization, raw reads, HITL, and Sol answer composition keep their existing boundaries.
- **Verification:** graph integration tests inject a RAG tool decider and confirm that focused selection reaches the existing runtime without activating full-document state.

## 2026-08-24 — Add explicit comprehensive-document graph path

- **Why:** Top-k semantic and lexical retrieval can omit distant sections when a user explicitly asks for complete-file review, requirement extraction, or cross-section consistency.
- **Behavior/contract impact:** The graph is now `general-assistant-checkpoint-v2` and detects explicit Korean/English comprehensive-document tasks. It resolves one currently authorized user-controllable document, reuses typed document selection for ambiguity, prepares compact coverage/citation state, recalls memory, and re-reads the same authorized range inside `respond_full_document` without checkpointing raw text. Up to 24,000 characters is complete coverage; larger documents currently answer from the first 12,000-character range with a mandatory localized partial-review disclosure. System documents cannot be selected, and replay preserves the original document instead of substituting another source.
- **API/operations impact:** Completed run responses/details expose nullable `document_coverage`, while redacted `full_document_read` events expose metadata, offsets, total characters, and latency. Explicit comprehensive intent is the baseline activation gate; older waiting runs are graph-version incompatible and should be drained or cancelled before rollout.
- **Verification:** `tests/test_full_document_retrieval.py` and `tests/test_settings.py` cover intent boundaries, complete/partial reads, permission and selection behavior, refresh/SSE/replay contracts, citation ranges, checkpoint raw-body exclusion, and settings validation. Automatic multi-range synthesis and token-aware budgeting remain follow-up work.

## 2026-08-17 — Add opt-in run persistence, document-selection HITL, and Store recall

- **Why:** The stable RAG/answer pipeline now needs genuine user interaction that can pause and resume without turning checkpoint state into a second conversation history.
- **Behavior/contract impact:** The product graph can compile with run-scoped PostgresSaver and PostgresStore. Ambiguous document requests return a safe document-selection interrupt and resume by exact authorized document ID. Checkpoint state contains bounded messages and primitive RAG snapshots; Product DB remains authoritative for transcripts, runs, permissions, citations, and memory governance. Store search results are revalidated against canonical memory rows, and reconciliation repairs projection drift.
- **Verification:** Offline graph/API/Store projection tests cover strict serialization, interrupt/resume, exact document citations, and idempotent reconciliation. PostgreSQL setup/restart smoke remains feature-gated and is run before production activation.

## 2026-08-12 — Hide ambient system-knowledge provenance from users

- **Why:** System knowledge was intended as ambient model context, but prompt context and public citations exposed its source identity.
- **Behavior/contract impact:** System chunks still reach answer composition, but graph/provider context keeps only their snippet text and explicitly forbids inferring omitted provenance. Personal/group provenance remains unchanged; the conversation API separately filters system citations and metadata from public responses.
- **Verification:** `tests/test_responders.py` and `tests/test_system_knowledge_base_user_type.py` cover snippet injection without system KB/document/source metadata.

## 2026-06-22 — Add source-selection gate and bind hosted web search without language-specific hints

- **Why:** App-side English keyword hints missed explicit web-search or no-KB requests written in other languages, and unconditional graph-owned RAG setup made common/web turns pass through ContextForge even when the user asked not to use saved documents.
- **Behavior/contract impact:** `graph.py` now runs `classify_request -> decide_retrieval_source -> retrieve_rag_context|skip_rag_context -> retrieve_memory -> respond_*`. OpenAI mode can use a thin LLM source-selection gate to decide KB retrieval versus common/web answering; deterministic mode keeps an offline fallback. Bypassed turns still return an explicit `no_retrieval` `RagAgentRetrievalResult` so API events and persistence keep one contract. Source selection is latest-turn-first but multi-turn-aware: follow-up-like turns can inherit recent web/current intent unless the latest turn introduces a new KB/document instruction. `OpenAIResponseProvider` exposes hosted `web_search` for both `general_assistant` and `research_helper` routes, while the provider prompt tells the model to call it only for current, recent, web-backed, source-backed, or externally verifiable requests, including follow-ups that inherit that source need. API response shape is unchanged.
- **Verification:** `tests/test_retrieval_gate.py`, `tests/test_graph.py`, and `tests/test_responders.py` cover source gating, explicit bypass, source-continuity follow-ups, runtime decider injection, route-level web-search binding, and provider prompt policy.

## 2026-06-16 — Invoke the RAG Agent inside the assistant graph

- **Why:** The general assistant should be the top-level controller and decide inside its graph when to retrieve document evidence, instead of receiving a fully preassembled ContextForge result from the API service.
- **Behavior/contract impact:** `graph.py` now runs `classify_request -> retrieve_rag_context -> retrieve_memory -> respond_*`. Clarification results continue to response composition so users receive visible assistant text plus the structured clarification contract; insufficient required evidence still halts before answer composition. `rag_retrieval.py` calls the runtime-only RAG Agent retrieval boundary supplied through LangGraph context, and API run paths persist retrieval/citation/grounding state from graph output.
- **Verification:** `uv run pytest -q tests/test_conversations_api.py tests/test_permission_aware_rag.py` covers sync, streaming, replay, permission, and safe no-evidence halt behavior.

## 2026-06-14 — Prioritize injected system knowledge in provider prompts

- **Why:** A system knowledge-base document could be retrieved and injected into graph state, but the OpenAI provider prompt was not explicit enough that direct system/project facts should be used by normal `general_assistant` answers.
- **Behavior/contract impact:** Authorized document context now carries knowledge-base and retrieval-source metadata into the provider prompt, and the prompt tells the model to answer from direct authorized context first for `my-agents`, project, and system-knowledge questions.
- **Verification:** `uv run pytest tests/test_system_knowledge_base_user_type.py::test_system_kb_project_context_injects_small_smoke_fact tests/test_responders.py::test_openai_provider_prioritizes_project_document_context_in_prompt -q` passed.

## 2026-06-14 — Document ambient system knowledge as retrieval context

- **Why:** System knowledge bases can now be included in authenticated conversation retrieval for public project facts, but that context must not be described as user memory or as graph-owned storage.
- **Behavior/contract impact:** `general_assistant` documentation now states that service-layer `retrieved_context` may contain ambient system/project knowledge. The agent graph still receives already-authorized document context only, does not manage system KB permissions, and keeps memory context in a separate channel.
- **Verification:** `uv run pytest -q tests/test_system_knowledge_base_user_type.py tests/test_context_forge_contracts.py tests/test_conversations_api.py` covered system retrieval, source boundaries, and existing run behavior.

## 2026-06-10 — Move memory recall into the LangGraph flow

- **Why:** The memory migration plan calls for recall to be graph-owned instead of preassembled by the FastAPI conversation service, while keeping Product DB governance intact until LangGraph Store is introduced.
- **Behavior/contract impact:** `graph.py` now runs `classify_request -> retrieve_memory -> respond_*`. `retrieve_memory` receives a runtime-only `MemoryRuntime` through LangGraph `context`, searches the current Product DB-backed adapter, and writes `memory_context` plus `source_conflicts` into graph state. API run paths pass runtime context and persist the internal memory-source audit snapshot from graph output/update state, including failed runs when memory recall completed before a later provider failure. Frontend-visible run events expose only public-safe memory counts/categories, not raw memory/source IDs. Checkpointer is still not enabled.
- **Verification:** `uv run pytest -q tests/test_graph.py tests/test_memory_service.py tests/test_conversations_api.py::test_conversation_run_injects_enabled_user_memory_and_conflicts tests/test_conversations_api.py::test_conversation_run_excludes_disabled_user_memory tests/test_conversations_api.py::test_streaming_conversation_run_emits_events_and_persists_result tests/test_conversations_api.py::test_streaming_conversation_run_emits_answer_deltas_before_completion tests/test_conversations_api.py::test_streaming_cancelled_run_does_not_persist_partial_assistant tests/test_conversations_api.py::test_streaming_assistant_message_replay_emits_deltas_and_prunes_after_success` passed.

## 2026-06-09 — Inject opt-in memory and source conflicts into provider context

- **Why:** The memory architecture now has durable per-user memory records and write policy gates, so conversation runs need to pass active memory into the responder without giving the agent folder direct persistence ownership.
- **Behavior/contract impact:** `graph.py` state accepts `memory_context` and `source_conflicts`; `responders.py` forwards them into `SourceContextBundle`; prompt guidance prefers the latest conversation over conflicting stored memory and authorized documents over conflicting memory for document-grounded claims. Disabled, sensitive, stale, inactive, deleted, invalid stable-preference-shaped, and query-irrelevant non-preference memories are filtered before graph invocation. Public memory writes do not accept client-asserted provenance/value/TTL payloads, document-delete staleness shares the document delete transaction, run audit stores redacted memory-source snapshots, and delete/reject/expiry/confirm scrub stored duplicate suggestion content/value.
- **Verification:** `uv run pytest -q tests/test_responders.py tests/test_conversations_api.py::test_conversation_run_injects_enabled_user_memory_and_conflicts tests/test_conversations_api.py::test_conversation_run_excludes_disabled_user_memory tests/test_memory_service.py tests/test_memory_api.py` passed for memory injection and conflict channels.

## 2026-06-09 — Make provider source context explicit

- **Why:** The memory architecture plan requires Product DB transcript state, future stored memory, authorized document context, and material source conflicts to remain separate. The OpenAI provider previously selected recent conversation context through an implicit `messages[-6:]` slice inside prompt construction.
- **Behavior/contract impact:** `context.py` now defines `SourceContextBundle` and an explicit recent Product DB conversation window for provider context. Stored memory and material conflict channels were added first, then populated by the later opt-in memory milestone. The current full-message graph still must not be compiled with a checkpointer as-is.
- **Verification:** `uv run pytest -q tests/test_responders.py` passed for the explicit context bundle and provider prompt policy.

## 2026-05-21 — Add retrieval-routing metadata to graph/provider state

- **Why:** Product conversation runs now decide retrieval use before graph invocation, and
  the agent needs explicit metadata to frame answers without owning retrieval or permission
  logic.
- **Behavior/contract impact:** `general_assistant` receives `retrieval_route`,
  `answer_mode`, `document_scope`, and already-authorized compact `retrieved_context` from
  the service layer. The graph/provider can frame `general_knowledge`,
  `document_grounded`, or `mixed` answers, but it still does not query document/vector
  storage directly.
- **Verification:** `uv run pytest tests/test_retrieval_routing.py tests/test_permission_aware_rag.py tests/test_conversations_api.py tests/test_agent_observability_evals.py tests/test_migrations.py tests/test_graph.py tests/test_responders.py -q` passed during the retrieval-routing implementation.

## 2026-05-21 — Classify route labels from the latest user turn only

- **Why:** Prior assistant or project-planning history could pollute a later document
  question and incorrectly route it as planning-oriented instead of `general_assistant`.
- **Behavior/contract impact:** Deterministic route-label classification now uses the
  latest user message only. Retrieval routing still independently detects uploaded-document
  questions such as `연말정산 관련 문서 업로드 했는데 내용좀 알려줘`.
- **Verification:** `tests/test_classifier.py` covers a regression where prior project
  history must not change the latest Korean uploaded-document question away from
  `general_assistant`.
