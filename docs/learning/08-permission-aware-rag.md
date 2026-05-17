---
created: 2026-05-17
updated: 2026-05-17
status: active
topics:
  - rag
  - permissions
  - citations
  - graphrag
related_code:
  - my_agents/api/conversations.py
  - my_agents/knowledge/retrieval.py
  - my_agents/knowledge/models.py
  - my_agents/conversations/schemas.py
  - tests/test_permission_aware_rag.py
---

# Permission-aware RAG and citation-backed answers

This note explains the first thin RAG slice for the portfolio chat service.

## What is implemented now

A product chat run can now answer with context from ingested personal or group documents.
The important rule is simple: retrieval starts from the user's authorized documents, not
from the entire knowledge corpus.

The current implementation is intentionally deterministic so tests stay offline:

- direct retrieval uses term matching over authorized chunks;
- graph expansion uses extracted entity mentions to add related authorized chunks;
- citations are stored against the `AgentRunModel`;
- the response payload returns citation IDs, document IDs, chunk IDs, and snippets.

## Request flow

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as Conversation run API
    participant Auth as Auth dependency
    participant R as RetrievalService
    participant G as LangGraph assistant
    participant DB as Database

    UI->>API: POST /conversations/{id}/runs
    API->>Auth: resolve current principal
    API->>DB: persist user message
    API->>R: retrieve(user_id, query)
    R->>DB: select chunks from authorized documents only
    R->>DB: expand through authorized entity mentions
    API->>G: invoke with server-owned messages and retrieved_chunk_ids
    API->>DB: persist assistant message, run, citations
    API-->>UI: reply + citations
```

## Why permission filtering happens first

RAG systems can leak data if they retrieve globally and filter later. Even if the final
answer hides unauthorized text, intermediate rankings, graph edges, tool traces, or model
context can expose private information.

This project uses a safer order:

```mermaid
flowchart LR
    Query[User query] --> AuthRows[Authorized document chunks]
    AuthRows --> Direct[Direct deterministic matches]
    Direct --> Entities[Entity IDs from matched chunks]
    Entities --> Expansion[Related authorized chunks]
    Expansion --> Compose[Citation-backed reply]
```

The graph expansion step also stays inside the authorized row set. This means a private
chunk can never become a citation or model context for an outsider just because it shares
an entity with an authorized chunk.

## Current limitations

- Retrieval scoring is a deterministic fixture, not pgvector ranking.
- The reply composition is a thin service-layer scaffold, not a polished answer synthesis prompt.
- Citations include snippets but not character offsets in the API response yet.
- Streaming is not implemented yet; structured run events are covered in the next note.
- The legacy `/assistant/chat` endpoint still exists for smoke checks and does not own product KB access.

These constraints are acceptable for this portfolio stage because they make the security
boundary testable before adding more impressive retrieval infrastructure.

## Testing evidence

`tests/test_permission_aware_rag.py` verifies:

- an owner receives citations and authorized context from an ingested private document;
- an outsider asking the same question receives no private citations or private reply text;
- graph expansion adds a related authorized chunk that shares an extracted entity with the direct match;
- the graph receives only retrieved chunk IDs, not raw unauthorized document text.

## Revision history

- 2026-05-17: Updated limitations after adding structured run events in the next slice.
- 2026-05-17: Created after adding thin permission-aware retrieval, graph expansion, and citations.
