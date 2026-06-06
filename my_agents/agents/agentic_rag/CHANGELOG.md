# Agentic RAG changelog

## 2026-06-06 — Add deterministic Agentic RAG workflow contract

- Why: the V1 story needs a named `agentic_rag` workflow surface while preserving ContextForge as the existing Retrieval Agent boundary.
- Behavior/contract impact: added deterministic stage planning and verification for compact localized traces; no retrieval, authorization, or provider execution moved into this package.
- Verification: covered by `tests/test_agentic_rag_contracts.py` and conversation API trace tests.
