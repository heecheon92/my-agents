# general_assistant CHANGELOG

This changelog records why the production-surface `general_assistant` agent folder needed
meaningful behavior, graph-state, provider, or documentation changes.

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
  question and incorrectly route it to `project_planner`.
- **Behavior/contract impact:** Deterministic route-label classification now uses the
  latest user message only. Retrieval routing still independently detects uploaded-document
  questions such as `연말정산 관련 문서 업로드 했는데 내용좀 알려줘`.
- **Verification:** `tests/test_classifier.py` covers a regression where prior project
  history must not change the latest Korean uploaded-document question away from
  `general_assistant`.
