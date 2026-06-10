# Retrieval agent hybrid reference

This document is the backend-owned reference for the retrieval-agent track. It intentionally keeps the design local to this repository so roadmap and implementation notes do not depend on files from other workspaces. Current production status: ContextForge owns the retrieval service boundary and now exposes a thin LangGraph RetrievalGraph wrapper over that service, while `rag_agent` owns a thin RAG Agent contract graph for trace/grounding verification. The deeper tool-using retrieval graph described here remains a future expansion of that wrapper, not a replacement for the permission-first service boundary.

## Goal

The retrieval agent track should keep document search quality out of the general assistant. ContextForge already takes a user query plus authorization context, produces trustworthy cited chunks, and exposes retrieval evidence. The current thin RetrievalGraph makes that capability callable as a typed graph/tool seam. Future work can promote more retrieval planning into graph/tool nodes when evals justify the added orchestration.

## Target pipeline

```mermaid
flowchart TD
    Query[User query] --> Plan[Retrieval query planner]
    Plan --> Auth[Permission-filtered candidate scope]
    Auth --> Vector[Vector candidate search]
    Auth --> FullText[Keyword/full-text candidate search]
    Auth --> Graph[Entity/relationship expansion]
    Plan --> Expand[Optional query expansion]
    Plan --> HyDE[Optional HyDE query document]
    Expand --> Vector
    HyDE --> Vector
    Vector --> Fuse[Weighted candidate fusion]
    FullText --> Fuse
    Graph --> Fuse
    Fuse --> Rerank[Optional cross-encoder reranker]
    Rerank --> Pack[Context packing and citation shaping]
    Pack --> Evidence[Retrieval events and eval facts]
    Evidence --> RAGAgent[RAG Agent trace and grounding contract]
    Evidence --> Assistant[General assistant answer composition]
    Assistant --> RAGAgent
```

## Current graph interface

Current conversation runs call:

```python
graph_result = invoke_context_forge_graph(
    db=db,
    request=ContextForgeRequest(
        user_id=user_id,
        conversation_id=conversation_id,
        query=message,
        messages=messages,
        selection_context=selection_context,
    ),
)
```

The wrapper returns the underlying `ContextForgeResult`, a bounded
`retrieval_attempt_count`, and an `insufficient_evidence` flag. It does not own
authorization, raw SQL, ingestion, final-answer composition, citations,
conversation persistence, or provider secrets.

## Future interface sketch

```python
@dataclass(frozen=True)
class RetrievalAgentRequest:
    user_id: str
    conversation_id: str
    query: str
    rewritten_query: str
    limit: int = 5


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    document_id: str
    text: str
    sources: tuple[str, ...]  # vector, fulltext, graph_expansion, hyde
    candidate_score: float
    rerank_score: float | None = None


class RetrievalAgent:
    def retrieve(self, request: RetrievalAgentRequest) -> list[RetrievalCandidate]:
        """Return permission-safe, ranked retrieval candidates for answer context."""
```

The current backend can grow toward this interface incrementally from
`invoke_context_forge_graph(...)`. The authorization filter remains
non-negotiable: every candidate generation path must start from chunks the
principal is allowed to read.

## Candidate generation stages

### 1. Vector search

- Embed the query or planned query.
- Search only authorized chunks.
- In local/offline mode, JSON-backed cosine ranking is acceptable.
- In production Postgres, pgvector should accelerate first-stage candidate retrieval.
- pgvector is not the final relevance judge; it is a fast candidate generator.

### 2. Keyword/full-text search

- Use lexical search for exact names, acronyms, quoted phrases, and Korean/English terms that embedding search may underweight.
- Combine with vector results through weighted fusion.
- Keep weights configurable later, but start with simple defaults.

### 3. Graph/entity expansion

- Use entity mentions and co-occurrence relationships to add adjacent authorized chunks.
- Treat expansion as recall support, not proof of final relevance.
- Expanded chunks should normally rank below strong vector/full-text matches unless reranking promotes them.

### 4. Cross-encoder reranking

- Rerank only a small top-k candidate set, for example 10-30 chunks.
- Score `(query, chunk_text)` pairs after permission filtering.
- Keep reranking behind a provider/interface flag so tests stay offline and local setup does not require model downloads.
- Persist or emit aggregate reranking metadata, not raw hidden reasoning.

### 5. Query expansion

- Generate synonyms and related concepts while preserving the user's original intent.
- Use expansion to improve candidate recall, not to broaden the task beyond the user's request.
- Track whether expanded terms changed the result set for eval/debugging.

### 6. HyDE

- For broad conceptual questions where direct hybrid search under-recovers, generate a short hypothetical answer/document and embed that for vector candidate search.
- Use HyDE as a fallback or optional branch, not as the default for every query.
- Keep generated hypothetical text out of citations; citations must point to real authorized chunks.

## Candidate fusion sketch

```python
combined_score = (
    vector_weight * normalized_vector_score
    + fulltext_weight * normalized_fulltext_score
    + graph_weight * graph_expansion_bonus
    + hyde_weight * normalized_hyde_score
)
```

Initial recommended defaults:

- vector: `0.55`
- full-text: `0.30`
- graph expansion: `0.10`
- HyDE: `0.05`, only when enabled for the query

These are starting points for evals, not product truths.

## Evaluation target

The retrieval-agent track should add small fixture evals before tuning weights:

- recall: did the expected supporting chunk appear in the candidate set?
- precision: how many returned chunks are actually relevant?
- leakage: did unauthorized documents stay out of candidates, reranker inputs, events, and citations?
- stability: do deterministic fixtures keep the same ranking across test runs?
- latency budget: candidate generation and reranking stay within a documented demo threshold.

## Near-term implementation mapping

Current completed slices should remain a foundation, not the final architecture:

1. Add embedding provider boundary.
2. Add real OpenAI embedding mode while keeping deterministic offline mode.
3. Rank authorized chunks by JSON-backed cosine similarity.
4. Add pgvector storage/search on Postgres while keeping JSON/SQLite fallback.
5. Keep event/source names truthful.
6. Add a thin ContextForge RetrievalGraph wrapper so future agents can call the
   same permission-first retrieval path as a typed subgraph/tool.
7. Leave deeper role-node retrieval planning, full-text fusion, ANN/vector index
   tuning, query expansion, HyDE, and broader eval-driven orchestration as
   explicit follow-up seams.
