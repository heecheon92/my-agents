# Agentic RAG workflow

[한국어](./README.md) | English

`agentic_rag` defines the production-facing workflow contract for document-grounded conversation runs. It does not replace ContextForge; it names ContextForge as the Retrieval Agent and provides deterministic planning/verification for compact frontend trace state.

## Current role

- Defines typed workflow stages for Query Cartographer, Source Warden, Candidate Scouts, Evidence Judge, Context Curator, Assistant Graph, and Answer Composer.
- Plans stage status from already-redacted service-layer counts.
- Verifies stage order, bilingual copy, ContextForge retrieval ownership, and redacted evidence keys.
- Does not perform database retrieval, authorization, ingestion, reranking, LLM calls, or provider-side reasoning.

## File structure

| File | Responsibility |
| --- | --- |
| `contracts.py` | Dataclass contracts, stage identifiers, role names, and expected stage order. |
| `planner.py` | Deterministic stage planner for compact run traces. |
| `verifier.py` | Deterministic safety/shape verifier for trace contracts. |
| `README.md` / `README.en.md` | Korean/English behavior and boundary docs. |
| `CHANGELOG.md` | Why this agent folder changed. |

## Graph or execution flow

```mermaid
flowchart TD
    Run[Conversation run metadata] --> Planner[DeterministicAgenticRagPlanner]
    Planner --> Q[Query Cartographer]
    Q --> W[Source Warden]
    W --> S[Candidate Scouts]
    S --> J[Evidence Judge]
    J --> C[Context Curator]
    C --> G[Assistant Graph]
    G --> A[Answer Composer]
    Planner --> Verifier[DeterministicAgenticRagVerifier]
```

## Route/tool/state meaning

- Retrieval-agent stages are owned by `ContextForge`.
- Assistant stages are owned by `GeneralAssistantGraph`.
- `completed`, `skipped`, and `waiting` are frontend trace states, not hidden chain-of-thought.
- Evidence values are counts, labels, and booleans only; raw prompts, snippets, provider errors, and message content are rejected by the verifier.

## Capability or boundary metadata

This is a production contract layer with deterministic behavior. It is not an autonomous agent runtime and has no provider credentials or external side effects.

## Relationship to service layers

API and conversation services pass already-authorized counts and route metadata into this package. Authorization, source selection, retrieval SQL, ingestion, persistence, citations, and provider execution stay in their existing service modules.

## Extension guidance

Add new workflow stages here only when the API can expose them with redacted evidence and tests. Do not move ContextForge retrieval internals, permissions, or provider-secret handling into this package.

## Change checklist

- Update `tests/test_agentic_rag_contracts.py` for contract changes.
- Run conversation API tests when trace payloads change.
- Keep this README pair and `CHANGELOG.md` aligned.
