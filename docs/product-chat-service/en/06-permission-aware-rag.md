---
created: 2026-05-17
updated: 2026-08-24
status: active
topics:
  - rag
  - permissions
  - citations
  - graphrag
related_code:
  - my_agents/api/conversations.py
  - my_agents/api/conversations/retrieval_context.py
  - my_agents/knowledge/retrieval.py
  - my_agents/knowledge/routing.py
  - my_agents/knowledge/models.py
  - my_agents/conversations/schemas.py
  - tests/test_permission_aware_rag.py
  - tests/test_full_document_retrieval.py
---

# Permission-aware RAG and citation-backed answers

This note explains the first thin RAG slice for the product chat service.

## What is implemented now

A product chat run can now answer with context from ingested personal or group documents.
The important rule is simple: retrieval starts from the user's authorized documents, not
from the entire knowledge corpus.

The current implementation is intentionally deterministic so tests stay offline:

- retrieval routing classifies each prompt as `no_retrieval`, `retrieval_required`, `retrieval_optional`, or `clarification_required`;
- direct retrieval uses term matching over authorized chunks only when routing calls for retrieval;
- graph expansion uses extracted entity mentions to add related authorized chunks;
- answer mode is explicit: `general_knowledge`, `document_grounded`, or `mixed`;
- ambiguous document references return a language-neutral `clarification` contract for
  human-in-the-loop localization instead of hard-coded English prose;
- filename/title-like document references are matched against authorized document metadata
  before body-only retrieval, so visible upload names can resolve even when absent from text;
- conversation runs enter retrieval through the `general_assistant` graph, which invokes the RAG Agent runtime; the RAG Agent delegates internally to the thin ContextForge LangGraph RetrievalGraph and records bounded retry/sufficiency state;
- citations are stored against the `AgentRunModel` only when retrieved chunks are actually used;
- the response payload returns routing metadata plus citation IDs, document IDs, chunk IDs, and snippets.
- an opt-in `comprehensive_document` path handles explicit English or Korean whole-document
  tasks without changing ordinary semantic/BM25 retrieval;
- the path resolves exactly one currently authorized user-controllable document, excludes
  ambient system documents, and reuses the durable `document_selection` interaction when
  more than one eligible document remains ambiguous;
- completed responses expose `document_coverage` as `complete` or `partial`, and citations
  come only from chunks overlapping the covered character range.

## Request flow

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as Conversation run API
    participant Auth as Auth dependency
    participant G as general_assistant LangGraph
    participant RAG as RAG Agent runtime
    participant CG as ContextForge RetrievalGraph
    participant R as RetrievalService
    participant DB as Database

    UI->>API: POST /conversations/{id}/runs
    API->>Auth: resolve current principal
    API->>DB: persist user message
    API->>G: invoke with server-owned messages + runtime RAG context
    G->>RAG: retrieve_rag_context
    RAG->>CG: invoke_context_forge_graph(request)
    CG->>R: retrieve(user_id, query) only for required/optional
    R->>DB: select chunks from authorized documents only
    R->>DB: expand through authorized entity mentions
    CG-->>RAG: ContextForgeResult + attempt/sufficiency state
    RAG-->>G: authorized context + route/answer mode
    alt clarification required
        G->>G: compose visible clarification reply
        G-->>API: reply + structured clarification state
        API->>DB: persist assistant message, run, clarification contract
        API-->>UI: clarification reply + clarification contract
    else insufficient evidence
        G-->>API: halt before answer node
        API->>DB: persist safe terminal run state
        API-->>UI: insufficient-evidence reply
    else retrieval/answer path
        G-->>API: reply + graph state
        API->>DB: persist assistant message, run, citations
        API-->>UI: reply + citations
    end
```

## Why permission filtering happens first

RAG systems can leak data if they retrieve globally and filter later. Even if the final
answer hides unauthorized text, intermediate rankings, graph edges, tool traces, or model
context can expose private information.

This project uses a safer order:

```mermaid
flowchart LR
    Query[User query] --> AuthRows[Authorized document chunks]
    AuthRows --> QueryEmbedding[Configured query embedding]
    QueryEmbedding --> Vector{Storage backend}
    Vector -->|Postgres pgvector| SQL[Permission-filtered SQL vector top-k]
    Vector -->|SQLite/tests fallback| JSON[JSON cosine ranking]
    SQL --> Direct[Lexical blended ranking]
    JSON --> Direct
    Direct --> Entities[Entity IDs from matched chunks]
    Entities --> Expansion[Related authorized chunks]
    Expansion --> Compose[Citation-backed reply]
```

The graph expansion step also stays inside the authorized row set. This means a private
chunk can never become a citation or model context for an outsider just because it shares
an entity with an authorized chunk.

## Full-document retrieval path

Full-document retrieval complements ranked chunk search for tasks where coverage matters
more than finding a few relevant passages. It is disabled by default and activates only
when the prompt contains both an explicit completeness phrase and a document task, for
example “review the entire document” or “문서 전체를 빠짐없이 검토해줘.” An ordinary
“summarize this document” request and a weak chunk-search result do not activate it.

```mermaid
flowchart TD
    Intent["Explicit comprehensive-document intent"] --> Gate{"Feature enabled and KB retrieval selected?"}
    Gate -->|No| Ranked["Normal permission-aware chunk retrieval"]
    Gate -->|Yes| Resolve["Resolve one authorized user-controllable document"]
    Resolve -->|Ambiguous| HITL["Existing document_selection interrupt"]
    HITL --> Resolve
    Resolve -->|Resolved| Read["Read normalized extracted text + overlapping chunks"]
    Read -->|At or below max chars| Complete["coverage = complete"]
    Read -->|Above max chars| Partial["first bounded range; coverage = partial"]
    Complete --> Answer["Compose answer and persist compact coverage metadata"]
    Partial --> Notice["Prepend mandatory partial-review notice"]
    Notice --> Answer
```

Target resolution uses the resumed `selected_document_id`, the only eligible document,
or one unique normalized title/source-filename match. If several documents remain, the
same versioned `document_selection` HITL contract used by ordinary ambiguous retrieval
pauses and resumes the run. Personal/group ownership, membership, explicit read grants,
the selected KB scope, and current permission are enforced in `RetrievalService`. Ambient
system KBs are never eligible targets or interaction options.

The read source is `DocumentModel.content`: the current normalized extracted text, not the
original upload bytes. Offsets are half-open character ranges `[start_offset, end_offset)`
in that text. With defaults, documents up to 24,000 characters are supplied completely;
larger documents supply characters `0..12,000` only. The completed response exposes:

```json
{
  "document_coverage": {
    "mode": "partial",
    "document_id": "...",
    "title": "...",
    "source_filename": null,
    "start_offset": 0,
    "end_offset": 12000,
    "total_chars": 48000
  }
}
```

`MY_AGENTS_FULL_DOCUMENT_MAX_CHARS` and
`MY_AGENTS_FULL_DOCUMENT_RANGE_CHARS` control those character budgets; the range must not
exceed the complete-read limit. The internal decimal continuation cursor is intentionally
not a public API field in this slice.

The graph prepares only compact coverage and chunk IDs, clears `retrieved_context`, and
then re-reads the authorized range inside `respond_full_document`. The raw body therefore
does not enter application checkpoints or run events. The full-body override is also
absent from the application logging path and LangSmith tracing is disabled around this
provider call. Existing opt-in DEBUG retrieval logging may still show its previously
documented bounded citation-chunk snippets; it does not receive the full-document body.

## Current limitations

- Retrieval routing is deterministic; Postgres ranking now uses pgvector SQL vector search after permission filtering, with JSON-backed cosine similarity as the SQLite/test fallback.
- LLM query planning, full-text fusion, and ANN/vector index tuning are still future work.
- The reply composition is a thin service-layer scaffold, not a polished answer synthesis prompt.
- Citation objects still do not expose a per-citation character range; `document_coverage`
  exposes the overall covered range instead.
- Large documents receive only the first configured range. There is no automatic cursor
  loop, per-range summary ledger, or final multi-pass whole-document synthesis yet.
- Budgets are characters in normalized extracted text, not provider-token estimates.
- Coverage offsets are coordinates in the current extracted-text revision. The service
  validates overlapping chunk offsets/content and re-reads before composition, but there
  is no durable document revision/hash contract yet; changed or stale chunk provenance
  can make the full-document path return insufficient evidence.
- At most 100 overlapping chunks may back one read. A range that exceeds that provenance
  bound is treated as unavailable instead of producing an uncited comprehensive answer.
- This path is explicit-intent-only and does not automatically retry a semantically weak
  ranked retrieval result with full-document access.
- Streaming exists, but frontend display of retrieval route/answer mode still belongs to the separate frontend repository.
- The legacy `/assistant/chat` endpoint still exists for smoke checks and does not own product KB access.

These constraints are acceptable for this demo stage because they make the security
boundary testable before adding more impressive retrieval infrastructure.

## RetrievalGraph augmentation milestone

The current production-shaped boundary is now: the Conversation API prepares the run request and runtime context, `general_assistant` invokes the RAG Agent inside its graph, the RAG Agent delegates to `invoke_context_forge_graph(...)`, ContextForge orchestrates the retrieval attempt and bounded sufficiency retry, and `RetrievalService` enforces permissions before returning packaged context. `general_assistant` receives authorized compact context plus `answer_mode` as graph state produced by the RAG Agent node.

Promote more of retrieval into graph nodes only when retrieval has enough internal
workflow to justify the added orchestration, for example:

- query rewrite;
- metadata/scope planning;
- hybrid full-text/vector search, candidate fusion, or ANN tuning behind the same permission boundary;
- cross-encoder reranking as a second-stage pass over top-k already-authorized candidates;
- reranking;
- context compression/packaging;
- product-facing retrieval quality profiles such as Fast / Balanced / Thorough, where
  candidate/vector limit, structured lookup, reranking, injected chunk count, and context
  char budget are tuned against measured latency and answer quality;
- richer clarification options, such as returning authorized document choices after a
  separate product/privacy review.

Even then, hard authorization should remain in `RetrievalService`, not in graph prompts,
agent reasoning, vector-store configuration, or cross-encoder prompts. The current and
future shape is:

```mermaid
flowchart LR
    API[Conversation API] --> GA[general_assistant graph]
    GA --> RAG[RAG Agent retrieval boundary]
    RAG --> RG[ContextForge RetrievalGraph]
    RG --> RS[RetrievalService authorization + search]
    RS --> RG
    RG --> RAG
    RAG --> GA
    GA --> Events[Citations + events]
```

## Testing evidence

`tests/test_permission_aware_rag.py` verifies:

- an owner receives citations and authorized context from an ingested private document;
- an outsider asking the same question receives no private citations or private reply text;
- graph expansion adds a related authorized chunk that shares an extracted entity with the direct match;
- no-retrieval prompts skip `RetrievalService.retrieve`;
- optional retrieval can fall back to `general_knowledge` when no relevant authorized chunks exist;
- the graph receives only retrieved chunk IDs/context that passed service-layer authorization, not raw unauthorized document text.

`tests/test_full_document_retrieval.py` verifies explicit-intent gating, owner and explicit
permission access, half-open cursor ranges, complete and partial coverage, overlapping
citations, ambiguous selection/resume, system-safe checkpoint/event persistence, buffered
partial-stream disclosure, refresh recovery, and replay without source substitution.

## Revision history

- 2026-08-24: Added the opt-in comprehensive-document path, coverage contract, privacy boundary, and bounded large-document limitations.
- 2026-06-17: Added future retrieval quality/speed profile guidance for balancing RAG accuracy with product UX latency.
- 2026-06-16: Promoted `rag_agent` to the assistant-facing retrieval boundary invoked from `general_assistant`, while ContextForge remains the delegated permission-first retrieval graph.
- 2026-06-10: Added the thin ContextForge RetrievalGraph wrapper as the active retrieval implementation seam while keeping deeper tool-using graph orchestration future-gated.
- 2026-05-25: Clarification-required runs now return structured human-in-the-loop state instead of deterministic English text.
- 2026-05-25: Added authorized title/source-filename metadata matching for filename-only document references.
- 2026-05-21: Added deterministic retrieval routing, answer modes, clarification route, and response/event metadata.
- 2026-05-17: Updated limitations after adding structured run events in the next slice.
- 2026-05-17: Created after adding thin permission-aware retrieval, graph expansion, and citations.
