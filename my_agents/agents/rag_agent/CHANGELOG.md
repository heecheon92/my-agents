# RAG Agent changelog

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
