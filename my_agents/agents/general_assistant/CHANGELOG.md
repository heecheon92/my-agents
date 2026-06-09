# general_assistant CHANGELOG

This changelog records why the production-surface `general_assistant` agent folder needed
meaningful behavior, graph-state, provider, or documentation changes.

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
