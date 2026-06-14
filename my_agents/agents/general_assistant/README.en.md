# general_assistant agent

[한국어](./README.md) | English

`general_assistant` is the current default LangGraph assistant/router implementation in this repository. It classifies the user's message into a deterministic route label, then the selected response node composes a reply through a shared response provider.

## Current role

- It is the single LangGraph reply path invoked by legacy FastAPI `/assistant/chat`, the terminal CLI, and product conversation runs.
- Route labels are metadata for choosing response behavior.
- Route labels are paired with `AgentCapability` metadata so replies can honestly reflect available tools, data sources, and side effects.
- Current route-specific nodes do not mean separate specialized agents executed.
- OpenAI reply generation goes through `langchain-openai` `ChatOpenAI`.
- deterministic mode remains available for tests and offline smoke checks.

## File structure

| File | Responsibility |
| --- | --- |
| `graph.py` | LangGraph `StateGraph`, nodes, conditional routing, graph state definition |
| `classifier.py` | Reads LangChain messages and produces deterministic `RouteDecision` values |
| `memory_recall.py` | Graph-owned memory recall node helpers and source-conflict detection |
| `context.py` | Assembles explicit provider source context from Product DB conversation messages, authorized document context, stored memory context, and material source conflicts |
| `responders.py` | deterministic/OpenAI response providers, OpenAI call boundary, future hosted tool policy location |
| `__init__.py` | Package boundary |

## Graph flow

```mermaid
flowchart TD
    Start([START]) --> Classify["classify_request"]
    Classify --> Memory["retrieve_memory"]
    Memory --> Route{"route label"}
    Route -->|general_assistant| General["respond_general"]
    Route -->|research_helper| Research["respond_research"]
    General --> Provider["response provider"]
    Research --> Provider
    Provider --> End([END])
```

## Route label meaning

| Label | Current meaning |
| --- | --- |
| `general_assistant` | General requests, organization, learning/planning/career help, practical next-step suggestions |
| `research_helper` | Research questions, source discovery, source-oriented answer direction |



## Capability metadata

`my_agents/agents/capabilities.py` records the route capability name, purpose, tools, data sources, and side effects. The graph attaches this metadata after classification, and `responders.py` includes it in deterministic replies and OpenAI prompts.

This keeps the API honest: `research_helper` may use hosted `web_search` in OpenAI mode, while `general_assistant` does not claim task-database or external project-management side effects.

## Relationship to the product service layer

The `general_assistant` folder owns the graph/classifier/responder boundary. Auth, group/document permissions, server-owned conversations, knowledge ingestion, retrieval selection, citations, and agent events are owned by service-layer modules such as `my_agents/api/`, `my_agents/knowledge/`, and `my_agents/conversations/`.

Product conversation runs now execute retrieval and RAG contract work before `general_assistant` writes prose. ContextForge retrieves authorized evidence, and `rag_agent` verifies compact trace/grounding contracts. `general_assistant` receives `retrieval_route`, `answer_mode`, `document_scope`, and compact `retrieved_context` from the service layer, then runs its own `retrieve_memory` node before response generation. The authorized document context may include ambient system knowledge that is public to authenticated chat users; it is still retrieval context, not user memory. The memory node receives a runtime-only `MemoryRuntime` adapter through LangGraph `context`, searches active user-scoped memory after opt-in/governance filtering, and writes compact `memory_context` plus `source_conflicts` into graph state. The graph/provider can adjust answer framing from that metadata, but it still does not query vector or document storage directly. Provider prompt construction goes through an explicit `SourceContextBundle`: recent Product DB conversation messages, opt-in stored memory, authorized document context, and material source conflicts are separate channels instead of an implicit hidden message slice. Permission decisions remain inside `RetrievalService` and the API/service layer; memory governance remains under `my_agents/memory/`.

```mermaid
sequenceDiagram
    participant RunAPI as conversation run API
    participant Retrieval as ContextForge / RetrievalService
    participant RAG as rag_agent contract graph
    participant Graph as general_assistant graph
    participant Provider as response provider
    participant Events as citations / events

    RunAPI->>Retrieval: route and retrieve authorized context
    Retrieval->>RAG: redacted evidence metadata
    RAG-->>Events: verified trace stages
    Retrieval-->>Graph: retrieval_route, answer_mode, retrieved_context
    RunAPI->>Graph: runtime MemoryRuntime via LangGraph context
    Graph->>Graph: retrieve_memory node writes memory_context and source_conflicts
    Graph->>Provider: compose with answer_mode
    Provider-->>Graph: reply
    Graph-->>RAG: reply and citation metadata
    RAG-->>Events: grounding check result
    Events-->>RunAPI: persisted reply, citations, trace
```

This separation matters for the product: LangGraph demonstrates AI reply flow, while the RetrievalService/API layer demonstrates production boundaries such as auth, permissions, and provenance. Ingestion (upload/parse/chunk/embed) remains a separate pipeline from retrieval routing.

The current ContextForge path already enters a thin `RetrievalGraph` wrapper before the assistant graph receives authorized context. Future work can deepen that wrapper into role-node/tool orchestration beyond the current RAG Agent contract graph, such as query rewrite, metadata planning, hybrid/vector search, reranking, or context compression. Even then, the hard authorization filter should remain inside `RetrievalService`, not in graph prompts.

## Conversation and source context assembly

`context.py` keeps provider context selection explicit. Product conversation runs still load the server-owned SQL transcript before graph invocation, but the provider receives a bounded recent conversation window through `SourceContextBundle` rather than directly depending on a hidden `messages[-6:]` slice in prompt construction.

Current channels are:

| Channel | Current source | Notes |
| --- | --- | --- |
| recent conversation | Product DB transcript passed into graph state | Product DB remains the visible transcript source of truth |
| stored memory | graph-owned `retrieve_memory` node using runtime `MemoryRuntime` | Disabled, sensitive, stale, inactive, deleted, invalid stable-preference-shaped, and query-irrelevant non-preference memories are excluded by the current Product DB-backed adapter |
| authorized documents | service-layer `retrieved_context` | Already permission-filtered before entering the graph; can include ambient system/project knowledge for authenticated users. The provider prompt treats direct answers in this channel as authoritative project context for `my-agents`/system-knowledge questions, including normal `general_assistant` replies. |
| material conflicts | graph-owned `source_conflicts` from `memory_recall.py` | Recent conversation is preferred over conflicting stored memory; authorized documents are preferred for document-grounded claims |

The memory service lives outside this agent folder under `my_agents/memory/` and `my_agents/api/memories.py`. Public memory writes do not accept client-asserted provenance IDs; service-owned paths must provide provenance when they create document-derived memories. The agent graph now owns recall orchestration, but persistence/governance still stays behind `MemoryRuntime`; graph state receives only serialized active memory context and conflict metadata, with memory/document snippets encoded as untrusted JSON prompt data. Replay/regeneration uses current active memory context rather than historical memory content. Completed and failed runs can retain an internal redacted memory-source audit snapshot, but frontend-visible run events expose only memory counts/categories/provenance types.

This is the first migration slice toward the LangGraph-native memory target: recall is graph-owned, but the runtime adapter still wraps the existing Product DB memory service. Remaining work is a separate `memory_graph` extraction/suggest-confirm workflow and LangGraph Store-backed active memory search while preserving Product DB governance for opt-in, provenance, source invalidation, and delete/deactivate. See [`docs/product-chat-service/en/19-langgraph-native-memory-migration.md`](../../../docs/product-chat-service/en/19-langgraph-native-memory-migration.md).

This is also the guardrail for future LangGraph checkpoint work: the current full-message graph should not be checkpointer-enabled as-is because Product DB transcript data would become duplicated in checkpoint state. Checkpointers should be run-scoped execution/HITL state, not conversation history or long-term memory.

## Where to add OpenAI hosted tools

OpenAI Responses API built-in tools such as `web_search` should be added at the **OpenAI provider boundary in `responders.py`, not directly inside graph nodes**.

Reasons:

- `graph.py` can stay focused on route decisions and flow control.
- Nodes such as `respond_general` and `respond_research` do not need to know provider-specific details.
- OpenAI-specific behavior stays inside `OpenAIResponseProvider`, making replacement and testing easier.
- route-specific tool policy can be tested in one place.

Recommended structure:

```text
graph.py
  -> decide route
  -> select response node
  -> pass route + guidance to provider

responders.py
  -> inspect route and choose OpenAI hosted tools
  -> apply ChatOpenAI.bind_tools([...]) when needed
  -> invoke model
  -> extract reply
  -> later extract citation/tool metadata
```

## Draft web search policy

This is not implemented yet. When implemented, start with a small step.

| Route | Default web search policy |
| --- | --- |
| `general_assistant` | Allow only when the user asks for current/recent/source-backed/web-searched information or current facts are required |
| `research_helper` | Allow by default |

The first implementation milestone should verify tool binding inside the provider without changing the API response schema. Add citations and tool metadata to `ChatResponse` only after confirming the real response shape.

## Change checklist

- If graph flow changes, check `tests/test_graph.py`.
- If routing keywords change, check `tests/test_classifier.py` and representative prompt fixtures.
- If response provider behavior changes, check `tests/test_responders.py`.
- OpenAI mode must remain testable without a real API key.
- When updating this README, update [`README.md`](./README.md) and this English file together.
