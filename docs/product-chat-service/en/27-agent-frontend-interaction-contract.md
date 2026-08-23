# Agent-to-frontend interaction contract

[한국어](../ko/27-agent-frontend-interaction-contract.md) | English

## Status and purpose

This document is the current architecture contract for user input that an agent run
needs before it can continue. The first implemented interaction is durable document
source selection. Future interactions must extend this contract instead of adding
one-off response fields, SSE event shapes, or frontend run-loop branches.

The same `document_selection` type serves both ordinary ambiguous document questions and
explicit comprehensive-document requests. Whole-document retrieval does not introduce a
new interaction type or expose its internal continuation cursor to the frontend.

The contract is deliberately protocol-neutral. The backend describes **what input is
required** and the frontend decides **how to render and collect it**. It does not add
AG-UI or A2UI as a dependency.

## Non-negotiable boundary

- Backend interactions are semantic, versioned, JSON-serializable, and safe to display.
- Backend payloads never name React components, layouts, colors, controls, or CSS.
- Ambient system knowledge is automatically injected server context, not a
  user-controllable source axis. System KBs and documents must never appear as interaction
  options, and a forged resume answer naming one is rejected.
- Frontend components are selected through a local interaction renderer registry.
- Activity events do not replace pending interaction state.
- Product DB run detail is the refresh-safe public source of truth; LangGraph checkpoints
  remain private run-scoped execution state.
- Raw prompts, provider traces, credentials, chain-of-thought, and unreviewed arbitrary
  dictionaries are forbidden in interaction payloads.

## V1 wire contract

All interaction requests and answers carry both a semantic type and a schema version.
The fields are required rather than inferred.

```json
{
  "schema_version": 1,
  "interaction_id": "<run_id>:document_selection",
  "type": "document_selection",
  "reason_code": "ambiguous_document_reference",
  "message_key": "clarification.document_scope.select_source",
  "expires_at": "2026-08-18T00:00:00Z",
  "option_count": 2,
  "options": [
    {
      "document_id": "...",
      "title": "Architecture notes",
      "source_filename": "architecture.pdf",
      "knowledge_base_id": "...",
      "knowledge_base_name": "Personal knowledge"
    }
  ],
  "next_cursor": null
}
```

Resume requests are type-specific rather than an open `payload` object:

```json
{
  "schema_version": 1,
  "interaction_id": "<run_id>:document_selection",
  "type": "document_selection",
  "document_id": "..."
}
```

The options endpoint repeats `schema_version`, `interaction_id`, and `type` so a page is
self-describing. Persisted `run_interrupted` and `run_resumed` activity events include
`interaction_schema_version`; streamed `run_interrupted` data contains the complete
waiting response.

## Lifecycle and durability

The default chat source mode remains all authorized personal/group KBs plus ambient system
knowledge. The interaction is not a replacement KB picker. It appears only when an
ambiguous document reference has more than one user-selectable document in the current
scope. One selectable document is resolved automatically, even when ambient system
documents also exist. A client-selected KB subset narrows the eligible document options.

For an explicit comprehensive-document request, “ambiguous” also includes a request that
names no unique title/source filename while several eligible documents exist. After resume,
the graph returns to full-document target resolution, revalidates that the selected
document is still user-controllable and authorized, and only then reads its bounded text.
System KBs/documents are excluded from both automatic target resolution and interaction
options; a forged system-document answer remains invalid.

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as Conversation API
    participant DB as Product DB
    participant Graph as LangGraph
    participant CP as Checkpointer

    UI->>API: POST run or run stream
    API->>Graph: invoke with thread_id = run_id
    Graph->>CP: checkpoint compact execution state
    Graph-->>API: interrupt(document_selection)
    API->>DB: persist waiting status and safe interaction
    API-->>UI: 202 or run_interrupted
    UI->>API: GET run detail after reload
    API-->>UI: waiting_for_input plus interaction
    UI->>API: POST resume with typed answer
    API->>DB: revalidate run, expiry, and document permission
    API->>Graph: Command(resume = document_id)
    Graph-->>API: completed or another interrupt
    API->>DB: persist canonical run result
    API-->>UI: completed or next waiting interaction
```

An unexpired waiting run remains the conversation's active run. A new run request is
rejected with HTTP `409` and `code=conversation_run_already_active`. Frontends must pause
queued messages while waiting instead of treating that response as ordinary retryable
stream backpressure.

The interaction can be reconstructed after refresh from `GET
/conversations/{conversation_id}/runs/{run_id}`. SSE is a transition signal, not the only
copy of state. Options are authorization-filtered when listed and the selected document
is authorized again when resumed. The option boundary is narrower than retrieval: it
contains only user-controllable personal/group documents, while ambient system knowledge
continues to support the answer automatically and without visible provenance.

`document_coverage` and `full_document_read` are result/audit contracts, not pending
interactions. They disclose complete/partial character coverage after a run completes and
must not be rendered as another question for the user. Large-document continuation is an
internal future workflow, not a V1 resume-answer field.

## Versioning and compatibility

- `schema_version=1` owns the common semantic envelope and current type payloads.
- A new interaction type may be added to V1 when it obeys the existing envelope.
- Optional display-safe fields may be added without a version bump.
- Removing a field, changing a field's meaning, or changing validation semantics requires
  a new schema version.
- Backend request schemas remain closed and reject unknown fields.
- Frontend parsing must recognize supported types and versions but retain an unsupported
  fallback that offers refresh and cancellation. Unknown data must not be rendered as raw
  JSON.

## Internal and adapter boundaries

```mermaid
flowchart LR
    Graph["LangGraph interrupt"] --> Domain["my_agents.interactions\nsemantic contract"]
    Domain --> API["REST and SSE adapter"]
    API --> Client["Frontend interaction parser"]
    Client --> Registry["type to local renderer registry"]
    Registry --> Card["DocumentSelectionCard"]
    Domain -. future .-> AGUI["AG-UI event adapter"]
    Domain -. future .-> A2UI["A2UI declarative UI adapter"]
```

[AG-UI](https://docs.ag-ui.com/) is an appropriate future boundary when the product needs
interoperable agent lifecycle/event transport. [A2UI](https://a2ui.org/) is an appropriate
future boundary only when an agent must describe dynamic declarative UI beyond the
maintained renderer catalog. Neither protocol may become the Product DB domain model.
Adapters translate at the transport or presentation edge and must preserve authorization,
redaction, versioning, localization, and accessibility rules.

## Adding another interaction type

1. Define a typed semantic model under `my_agents/interactions/` with `extra="forbid"`.
2. Add it to the `PendingInteraction` extension point and define a type-specific answer.
3. Persist only the public-safe interaction; keep framework checkpoint state private.
4. Revalidate authorization, expiry, current resource state, and that the answer names a
   user-controllable source rather than ambient system knowledge during resume.
5. Extend OpenAPI, REST/SSE events, and stable error-code coverage.
6. Add a frontend parser member, registry entry, localized copy, accessible renderer, and
   unsupported fallback coverage.
7. Test interrupt, reload recovery, pagination if applicable, resume, repeated interrupt,
   cancellation, expiry, denial, double submission, and feature-disabled parity.
8. Update this English/Korean contract and both repositories' concise agent rules.

## Current rollout gate

Do not enable shared-environment checkpointer interactions until all of these are true:

1. Alembic migration `20260817_0032` is applied.
2. `python -m scripts.langgraph_persistence setup` has initialized LangGraph Postgres
   schemas.
3. Memory-store reconciliation reports zero drift when the store flag is enabled.
4. The frontend version with waiting-state parsing, refresh recovery, source choice,
   resume routes, and held-queue behavior is deployed.

Keep `MY_AGENTS_FULL_DOCUMENT_RETRIEVAL_ENABLED=false` until this interaction rollout is
available wherever a comprehensive request may need document selection. Single-target
automatic resolution does not relax the same authorization and system-KB exclusion rules.

The interaction layer does not add a new database migration. `schema_version` is stored in
the existing public interaction JSON.
