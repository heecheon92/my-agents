# General Assistant and RAG Agent architecture change report

Date: 2026-06-16

Status: implemented in the `codex/g001-general-assistant-rag-agent` PR branch

Primary implementation commit: `0e11f35` (`Let the assistant own RAG retrieval decisions`)

This report explains the G001 architecture correction: the General Assistant is now the
product conversation controller, and the RAG Agent is the assistant-invoked retrieval
specialist boundary. ContextForge still exists, but it is now deliberately framed as the
internal permission-first retrieval engine behind the public RAG Agent runtime.

The practical result is that product conversation runs no longer precompute document
retrieval in the API service and pass it into the assistant graph. Instead, the API
constructs runtime-only dependencies, invokes the General Assistant graph, and the graph
itself decides the retrieval point by calling the RAG Agent before memory recall and
answer composition.

## 1. Architecture goal

The owner goal was not to delete ContextForge immediately. The goal was to fix the public
agent boundary:

- **General Assistant** should be the top-level ReAct-style controller for the user turn.
- **RAG Agent** should be a callable specialist tool/subgraph boundary for document-grounded
  retrieval.
- **ContextForge** should remain an internal delegated implementation until there is enough
  reason to split or replace it.
- Conversation APIs should stop making retrieval decisions as a pre-graph service-layer
  side effect.
- Existing safety properties must stay intact: authorization-first retrieval, citation
  persistence, insufficient-evidence fallback, clarification contracts, redacted events,
  streaming parity, deterministic tests, and no frontend contract break.

In short: the product should read as `General Assistant -> RAG Agent -> ContextForge`, not
`API -> ContextForge -> General Assistant`.

## 2. Before and after

Before this change, product runs prepared retrieval context before graph invocation. That
made the graph look like a reply writer receiving already-retrieved documents instead of a
controller that can choose whether to call retrieval.

```mermaid
flowchart LR
    subgraph Before[Before G001]
        UI1["Frontend"] --> API1["Conversation API"]
        API1 --> Select1["Resolve KB selection"]
        Select1 --> Precompute1["Service precomputes retrieval context"]
        Precompute1 --> CF1["ContextForge retrieval"]
        CF1 --> Input1["Graph input includes retrieved_context"]
        Input1 --> GA1["general_assistant composes reply"]
    end

    subgraph After[After G001]
        UI2["Frontend"] --> API2["Conversation API"]
        API2 --> Select2["Resolve KB selection"]
        Select2 --> Runtime2["Build graph runtime context"]
        Runtime2 --> GA2["general_assistant controller graph"]
        GA2 --> RAG2["RAG Agent runtime"]
        RAG2 --> CF2["ContextForge delegated engine"]
        CF2 --> GA2
        GA2 --> Persist2["Persist reply, citations, events"]
    end
```

The after shape is intentionally conservative: the RAG Agent boundary is now real and
callable from the graph, while ContextForge continues to do the low-level retrieval work
that was already tested.

## 3. New component responsibilities

```mermaid
flowchart TD
    User["Authenticated user"] --> API["Conversation API"]
    API --> Selection["KnowledgeBaseSelectionContext"]
    API --> GraphContext["LangGraph runtime context"]
    GraphContext --> RuntimeDeps["SqlAlchemyRagAgentRuntime + SqlAlchemyMemoryRuntime"]
    API --> GA["general_assistant graph"]

    GA --> Classifier["classify_request"]
    Classifier --> RAGNode["retrieve_rag_context"]
    RAGNode --> RAGRuntime["RAG Agent runtime contract"]
    RAGRuntime --> CFGraph["ContextForge RetrievalGraph"]
    CFGraph --> RetrievalService["RetrievalService"]
    RetrievalService --> DB[(Product database)]
    DB --> RetrievalService
    RetrievalService --> CFGraph
    CFGraph --> RAGRuntime
    RAGRuntime --> RAGNode
    RAGNode --> MemoryNode["retrieve_memory"]
    MemoryNode --> Responder["respond_general or respond_research"]
    Responder --> API
    API --> Persistence["Run, message, event, citation persistence"]

    classDef controller fill:#e8f3ff,stroke:#2f6fab,stroke-width:1px;
    classDef specialist fill:#eef9ec,stroke:#3a7c3a,stroke-width:1px;
    classDef internal fill:#fff7df,stroke:#a66a00,stroke-width:1px;
    classDef store fill:#f3e8ff,stroke:#7048a8,stroke-width:1px;
    class GA,Classifier,RAGNode,MemoryNode,Responder controller;
    class RAGRuntime specialist;
    class CFGraph,RetrievalService internal;
    class DB,Persistence store;
```

| Layer | Owns now | Does not own |
| --- | --- | --- |
| Conversation API | Authentication, conversation/run creation, KB selection resolution, runtime dependency construction, persistence, SSE response framing | Retrieval decision logic, prompt-visible document context construction |
| General Assistant | Top-level graph control flow, route classification, RAG Agent invocation, memory recall ordering, answer node selection | Raw SQL retrieval, authorization rules, citation row creation |
| RAG Agent | Public retrieval specialist runtime seam, `RagAgentRetrievalResult`, answer-ready retrieved context shaping, insufficient-evidence flags | Final answer prose, raw provider secrets, unauthenticated KB access |
| ContextForge | Delegated permission-first retrieval implementation, query planning, candidate search/fusion/reranking, evidence capture | Public assistant identity, final answer composition |
| RetrievalService | Hard authorization and data retrieval over product storage | Agent orchestration or prompt policy |

## 4. Main files changed and why

| File or area | What changed | Why it matters |
| --- | --- | --- |
| `my_agents/agents/general_assistant/graph.py` | Added a retrieval-enabled product graph and a separate no-KB legacy graph. Product graph order is now `classify_request -> retrieve_rag_context -> retrieve_memory -> respond_*`. | Makes General Assistant the graph owner of retrieval timing while preserving legacy smoke paths without KB access. |
| `my_agents/agents/general_assistant/rag_retrieval.py` | Added the graph node that calls the RAG Agent runtime from LangGraph runtime context and maps the result into assistant state. | Keeps retrieval inside the graph without serializing DB sessions or adapter objects into checkpointable state. |
| `my_agents/agents/rag_agent/retrieval.py` | Added `RagAgentRuntime`, `SqlAlchemyRagAgentRuntime`, and `RagAgentRetrievalResult`. | Creates the public specialist seam that future General Assistant tool logic can call. |
| `my_agents/api/conversations/graph_invocation.py` | `graph_context_for_run(...)` now builds both memory and RAG runtime dependencies. | Moves runtime-only DB-backed dependencies out of graph input and into LangGraph context. |
| `my_agents/api/conversations/retrieval_context.py` | Removed pre-graph retrieval preparation helpers and replaced them with graph-state adapters. | Makes graph output, not API prework, the source of retrieval truth. |
| Sync, stream, and replay run paths | Invoke the graph with runtime context and read `rag_retrieval_result` from graph updates/final state. | Preserves behavior across all conversation modes and avoids a sync-only architecture. |
| `my_agents/api/assistant.py` and `my_agents/cli.py` | Use `build_legacy_chat_graph()` for legacy unauthenticated chat. | Prevents `/assistant/chat` and CLI smoke paths from accidentally needing KB runtime context. |
| Agent READMEs and product docs | Updated terminology: RAG Agent is public retrieval boundary; ContextForge is delegated engine. | Aligns implementation, docs, and owner intent. |
| Tests and spies | Added graph/RAG runtime spy helpers and assertions that old precompute helpers are gone. | Locks the architectural boundary, not only happy-path replies. |

## 5. Runtime context instead of graph input precomputation

The implementation deliberately uses LangGraph runtime context for non-checkpointed
services. Graph input remains small and serializable: messages, principal IDs, conversation
IDs, and default state fields. Runtime context carries DB-backed adapters.

```mermaid
classDiagram
    class AssistantRuntimeContext {
        user_id
        memory_runtime
        rag_runtime
        knowledge_base_selection
    }

    class RagAgentRuntime {
        <<Protocol>>
        +retrieve_context()
    }

    class SqlAlchemyRagAgentRuntime {
        db
        +retrieve_context()
    }

    class RagAgentRetrievalResult {
        decision
        answer_mode
        retrieved_chunks
        retrieval_latency_ms
        knowledge_base_selection
        retrieval_evidence
        retrieval_attempt_count
        insufficient_evidence
    }

    class ContextForgeRequest {
        user_id
        conversation_id
        query
        messages
        selection_context
    }

    AssistantRuntimeContext --> RagAgentRuntime : provides
    SqlAlchemyRagAgentRuntime ..|> RagAgentRuntime : implements
    SqlAlchemyRagAgentRuntime --> ContextForgeRequest : builds
    RagAgentRuntime --> RagAgentRetrievalResult : returns
```

This matters because graph state may be streamed, logged, merged, tested, or eventually
checkpointed. A SQLAlchemy session and DB-backed runtime object should not live in that
state. The runtime context approach gives the graph control over when to call retrieval
without making runtime dependencies part of durable state.

## 6. Product conversation sequence

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as Conversation API
    participant DB as Product DB
    participant G as general_assistant graph
    participant RAG as RAG Agent runtime
    participant CF as ContextForge RetrievalGraph
    participant RS as RetrievalService
    participant RP as Response provider

    UI->>API: POST /conversations/{id}/runs
    API->>DB: Persist user message and started run
    API->>DB: Resolve authorized KB selection
    API->>G: invoke graph with messages and runtime context
    G->>G: classify_request
    G->>RAG: retrieve_rag_context
    RAG->>CF: invoke_context_forge_graph(request)
    CF->>RS: plan and retrieve from authorized scope
    RS->>DB: Query readable chunks only
    DB-->>RS: Authorized candidates
    RS-->>CF: RetrievedChunk list
    CF-->>RAG: ContextForge result and evidence
    RAG-->>G: RagAgentRetrievalResult
    alt clarification required
        G->>G: retrieve_memory
        G->>RP: compose visible clarification with route and RAG state
        RP-->>G: clarification reply
        G-->>API: final graph state with clarification route
        API->>DB: Persist assistant message, events, clarification contract
        API-->>UI: clarification reply and contract
    else insufficient evidence
        G-->>API: graph state halts before answer node
        API->>DB: Persist safe terminal run state
        API-->>UI: insufficient-evidence response
    else enough context or no retrieval needed
        G->>G: retrieve_memory
        G->>RP: compose response with route, RAG context, memory context
        RP-->>G: reply
        G-->>API: final graph state
        API->>DB: Persist assistant message, events, citations
        API-->>UI: completed run response
    end
```

The sequence shows the important inversion: the API does not call ContextForge first. It
only prepares the authorized selection and dependency context. The graph call owns the
RAG Agent invocation.

## 7. Graph control flow

```mermaid
flowchart TD
    Start([START]) --> Classify["classify_request"]
    Classify --> RetrieveRAG["retrieve_rag_context"]
    RetrieveRAG --> Decision{"RAG result can continue?"}
    Decision -->|"insufficient_evidence"| End2([END])
    Decision -->|"continue, including clarification_required"| Memory["retrieve_memory"]
    Memory --> Route{"Route label"}
    Route -->|"general_assistant"| General["respond_general"]
    Route -->|"research_helper"| Research["respond_research"]
    General --> End3([END])
    Research --> End4([END])
```

There are two graph variants:

1. `build_graph()` - product conversation graph with RAG Agent retrieval. Conversation
   services must invoke this graph with `graph_context_for_run(...)`.
2. `build_legacy_chat_graph()` - no-KB legacy graph for `/assistant/chat` and CLI smoke
   checks. This protects unauthenticated/dev paths from accidentally gaining document
   retrieval access.

The product graph intentionally runs RAG before memory recall. That ordering keeps
document-grounded evidence and answer mode available before the response provider is
called, while preserving the existing memory recall node as another graph-owned context
source.

## 8. Result and state mapping

The RAG Agent returns one product-facing retrieval result object. The General Assistant
node converts that object into assistant state fields used by downstream nodes and API
persistence.

```mermaid
flowchart LR
    RAGResult["RagAgentRetrievalResult"] --> Decision["decision.route"]
    RAGResult --> Mode["answer_mode"]
    RAGResult --> Chunks["retrieved_chunks"]
    RAGResult --> Evidence["retrieval_evidence"]
    RAGResult --> Attempts["retrieval_attempt_count"]
    RAGResult --> Sufficiency["insufficient_evidence"]

    Chunks --> Filter["chunks_used_for_answer"]
    Filter --> IDs["retrieved_chunk_ids"]
    Filter --> PromptContext["retrieved_context"]
    Sufficiency --> Halt["rag_halt_before_response"]
    Decision --> Continue["clarification can still compose reply"]
    Evidence --> Events["retrieval_completed event"]
    IDs --> Citations["citation persistence"]
    PromptContext --> Response["response provider context"]
```

The adapter layer in `my_agents/api/conversations/retrieval_context.py` now reads this
object from graph state and adapts it to the existing conversation service shape. That
keeps existing persistence code reusable while changing the authority source from
pre-graph service work to graph-owned RAG Agent output.

## 9. Sync, streaming, and replay parity

The change was made across all run paths, not only the normal synchronous endpoint.

```mermaid
flowchart TD
    Input["Conversation messages"] --> Context["graph_context_for_run"]
    Context --> Sync["Sync run lifecycle"]
    Context --> Stream["SSE run stream"]
    Context --> Replay["SSE replay stream"]

    Sync --> Invoke["invoke_graph_runner_collecting_updates"]
    Stream --> StreamItems["stream_graph_items"]
    Replay --> ReplayItems["stream_graph_items"]

    Invoke --> Extract1["retrieval_context_from_graph_state"]
    StreamItems --> Extract2["retrieval_context_from_graph_state"]
    ReplayItems --> Extract3["retrieval_context_from_graph_state"]

    Extract1 --> Persist1["events, citations, terminal run"]
    Extract2 --> Persist2["retrieval_completed SSE + run events"]
    Extract3 --> Persist3["replay SSE + replacement reply"]
```

Streaming required extra care because graph updates can arrive before final result state.
The stream adapter now forwards update fields such as `rag_retrieval_result`,
`rag_halt_before_response`, `retrieval_route`, `answer_mode`, and `document_scope`. That
lets SSE emit `retrieval_completed` as soon as the retrieval node has produced evidence,
while still waiting for answer deltas only when the graph continues to a response node.

## 10. Halt and completion states

```mermaid
stateDiagram-v2
    [*] --> RunStarted
    RunStarted --> RetrievalCompleted: RAG Agent returns decision
    RetrievalCompleted --> InsufficientEvidenceCompleted: required evidence is insufficient
    RetrievalCompleted --> AnswerComposition: retrieval is safe to continue or needs clarification
    AnswerComposition --> ClarificationCompleted: visible clarification + structured contract
    AnswerComposition --> GroundingVerification
    GroundingVerification --> CompletedWithCitations: verifier passes
    GroundingVerification --> InsufficientEvidenceCompleted: required retrieval retry exhausted
    GroundingVerification --> Failed: verifier rejects unsafe state
    RunStarted --> Failed: provider or graph error
    RunStarted --> Cancelled: cancellation requested
    AnswerComposition --> Cancelled: cancellation requested
    ClarificationCompleted --> [*]
    InsufficientEvidenceCompleted --> [*]
    CompletedWithCitations --> [*]
    Failed --> [*]
    Cancelled --> [*]
```

The halt states are part of the safety story:

- `clarification_required` returns visible assistant text plus a structured clarification
  contract, so the product UI is not left with a successful empty reply.
- `insufficient_evidence` persists a safe no-evidence answer and no citations.
- Required retrieval still goes through RAG Agent grounding verification before a cited
  reply is accepted.

## 11. Trust and privacy boundaries

```mermaid
flowchart TD
    UserQuery["User query"] --> AuthUser["Authenticated principal"]
    AuthUser --> Selection["Resolved KB selection"]
    Selection --> RAG["RAG Agent boundary"]
    RAG --> CF["ContextForge delegated engine"]
    CF --> AuthFilter["RetrievalService permission filter"]
    AuthFilter --> CandidateSet["Authorized candidate chunks only"]
    CandidateSet --> Rank["rank, fuse, rerank, pack"]
    Rank --> Redact["Redacted evidence"]
    Rank --> Context["Prompt-safe retrieved_context"]
    Context --> Assistant["General Assistant response provider"]
    Redact --> Events["Run events and trace payloads"]
    Assistant --> Citations["Persisted citations"]

    Blocked["Unauthorized chunks"] -. never enter .-> CandidateSet
    Secrets["Provider secrets and DB sessions"] -. never serialize into .-> Events
```

The permission boundary did not move into prompts. It remains in deterministic service
code. The graph calls the RAG Agent, the RAG Agent delegates to ContextForge, and
ContextForge still relies on RetrievalService for hard authorization before ranking,
expansion, reranking, packing, events, or citations can happen.

This is why the change is architectural but not a security relaxation. The public
assistant-facing name changed, and the control point moved into the graph, but the data
access rule stayed the same: never retrieve globally and filter later.

## 12. Frontend and API contract impact

No frontend implementation was needed for this change. The backend still emits the same
product-level concepts the frontend already understands:

- run started;
- user message stored;
- retrieval completed;
- graph invoked;
- answer deltas for streaming runs;
- answer composed;
- run completed or failed;
- citations, snippets, source metadata, route metadata, answer mode, warnings, and
  clarification/insufficient-evidence flags.

The important API-visible behavior is continuity. The internal source of retrieval context
changed from precomputed graph input to graph-produced RAG Agent state, but the persisted
run/event/citation contract remained stable.

## 13. Why ContextForge was kept

The original idea for a RAG Agent was to replace ContextForge. During implementation, the
more manageable path was to keep ContextForge as the delegated engine and move the public
boundary above it.

That choice avoided unnecessary churn because ContextForge already owned tested behavior:

- deterministic route planning;
- authorized source-boundary handoff;
- title/source-filename matching;
- structured entity retrieval;
- candidate fusion and optional reranking;
- context packing;
- bounded insufficient-evidence retry;
- redacted retrieval evidence;
- opt-in debug traces.

Replacing all of that in one step would have mixed two concerns: public architecture
correction and retrieval-engine rewrite. This PR corrects the architecture first. Future
work can gradually move more planning into RAG Agent nodes or replace ContextForge pieces
behind the same `RagAgentRuntime` seam.

## 14. Future extension path

```mermaid
flowchart LR
    GA["general_assistant ReAct controller"] --> ToolChoice{"Need external context?"}
    ToolChoice -->|"No"| DirectAnswer["Answer from conversation and memory"]
    ToolChoice -->|"Documents"| RAG["RAG Agent tool/subgraph"]
    ToolChoice -->|"Future tool"| OtherTool["Other specialist tool"]
    RAG --> Runtime["RagAgentRuntime seam"]
    Runtime --> CF["Current ContextForge engine"]
    Runtime --> FutureNodes["Future RAG planning nodes"]
    FutureNodes --> QueryRewrite["query rewrite"]
    FutureNodes --> Hybrid["full-text/vector fusion"]
    FutureNodes --> HyDE["optional HyDE"]
    FutureNodes --> Eval["retrieval eval feedback"]
    CF --> Evidence["authorized evidence"]
    FutureNodes --> Evidence
    Evidence --> GA
```

Near-term follow-up should happen behind stable seams:

1. Add explicit tool-selection policy in General Assistant when the product has more than
   one real tool.
2. Grow the RAG Agent runtime into richer retrieval nodes only when evals show the current
   ContextForge engine is the limiting factor.
3. Keep ContextForge internals replaceable by maintaining `RagAgentRuntime` as the public
   interface.
4. Keep authorization in service code, not in agent prompts.
5. Keep frontend/API payloads stable unless a future feature explicitly needs new public
   fields.

## 15. Verification evidence

The implementation PR was validated with the following evidence after the architecture
split was complete:

```text
uv run ruff check . --no-cache
uv run ruff format --check .
git diff --check
uv run python -m compileall -q my_agents tests
uv run pytest -q
# 420 passed, 2 skipped, 9 warnings
```

Additional review evidence recorded before this report:

- independent code review: approved with no findings;
- architecture review: clear;
- current PR branch pushed at `0e11f35`;
- no frontend changes required because the API/SSE product surface remained compatible.

## 16. What to review in the PR

Reviewers should focus on these questions:

1. Does `general_assistant` read as the top-level controller in both code and docs?
2. Is `rag_agent.retrieval.RagAgentRuntime` the right public seam for future tool/subgraph
   growth?
3. Are runtime-only dependencies kept out of graph state and persisted payloads?
4. Do sync, stream, and replay paths all extract retrieval context from graph state?
5. Does the legacy no-KB chat graph keep `/assistant/chat` and CLI behavior safe for
   unauthenticated smoke usage?
6. Are ContextForge docs clear that it is now delegated internals, not the public
   assistant-facing retrieval agent?
7. Are clarification, insufficient-evidence, citation, and redaction paths preserved?

## 17. Summary of this report

This change corrected the agent architecture without rewriting the whole retrieval engine.
The General Assistant is now the product graph controller and invokes the RAG Agent from
inside the graph. The RAG Agent exposes a typed runtime retrieval seam and returns a
single `RagAgentRetrievalResult` that downstream graph and API code can adapt into events,
answer context, citations, and safety states. ContextForge remains valuable, but it is now
framed as the internal permission-first retrieval engine delegated by the RAG Agent.

The API still owns authentication, KB selection resolution, persistence, and SSE framing;
it no longer owns pre-graph retrieval context construction. Runtime-only DB-backed
adapters are supplied through LangGraph context, not serialized graph input. Sync,
streaming, and replay paths all read retrieval results from graph updates/final state, so
behavior stays consistent across conversation modes.

Safety boundaries were preserved: RetrievalService still filters by authorization before
ranking or context packing, insufficient RAG evidence can still halt the graph before
answer composition, clarification results produce visible assistant text plus a structured
contract, grounding verification still protects required retrieval answers, and frontend
payloads remain stable. The architecture is now ready for a more ReAct-like General
Assistant that can choose whether to call the RAG Agent or other future specialist tools,
while the current tested ContextForge retrieval engine continues to operate behind the
new public boundary.
