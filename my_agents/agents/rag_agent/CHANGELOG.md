# RAG Agent changelog

## 2026-06-07 — Add dedicated graph form

- Why: the RAG Agent should be graph-shaped when it is a concrete agent contract, but the graph must not absorb retrieval, authorization, ingestion, persistence, or provider execution.
- Behavior/contract impact: added `graph.py` with a deterministic LangGraph `plan_workflow -> verify_workflow` path and routed compact trace generation through the verified graph output.
- Verification: covered by `tests/test_rag_agent_contracts.py`.

## 2026-06-06 — Add deterministic RAG Agent workflow contract

- Why: the V1 agentic RAG story needs a named concrete `rag_agent` surface while preserving ContextForge as the existing Retrieval Agent boundary.
- Behavior/contract impact: added deterministic stage planning and verification for compact localized traces; no retrieval, authorization, or provider execution moved into this package.
- Verification: covered by `tests/test_rag_agent_contracts.py` and conversation API trace tests.
