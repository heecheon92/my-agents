# Runtime configuration and frontend integration reference

[한국어](../ko/34-runtime-and-integration-reference.md)

This page collects the runtime options, operator steps, and frontend-facing contract notes that are
too detailed for the root README. Each section links to the topic document that owns the full
contract.

## Response modes and model boundaries

- `MY_AGENTS_RESPONSE_MODE=deterministic` runs without any provider credentials and disables
  external decision calls.
- For real OpenAI responses, set `OPENAI_API_KEY` in `.env` and use
  `MY_AGENTS_RESPONSE_MODE=openai`. Ordinary responses use the `langchain-openai` / `ChatOpenAI`
  boundary. The optional temporary document workspace is the isolated exception: it uses a narrow
  OpenAI SDK adapter for Files, Containers, Hosted Shell, and Skills.
- Source selection, retrieval-tool selection, and ContextForge intent use a separate bounded
  decision provider. Its configuration, fallback rules, and data-sharing limits are documented in
  the [RAG Agent README](../../../my_agents/agents/rag_agent/README.en.md).
- The assistant keeps a stable `my-agents` identity anchored to the canonical
  `https://my-agents.dev` domain, and grounds changing product facts in authorized context.
- When asked which model they are interacting with, the assistant discloses its configured model
  ID. The ordinary chat system prompt includes the same `MY_AGENTS_OPENAI_MODEL` value used for API
  requests, so changing it and restarting the backend updates the prompt. The value is the
  configured model ID, not a provider-resolved snapshot.
- Ordinary answers use a warm, approachable tone: brief for simple questions, more detailed for
  learning and complex questions. The server default is `MY_AGENTS_OPENAI_VERBOSITY=medium`
  (`low`/`high` remain configurable); existing environment overrides still win and the output-token
  budget is unchanged. Per-user style controls are
  [proposed](../../idea/assistant-behavior-preferences.md), not implemented.

## Run reasoning preferences

Registered-account run requests may optionally provide `reasoning_mode` (`standard` or `pro`) and
`reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`). When omitted,
mode is `standard` and effort comes from `MY_AGENTS_OPENAI_REASONING_EFFORT`. Guests are always
fixed to `standard` and the environment default effort. `GET /capabilities/reasoning` reports the
effective default and configured-model support. `pro` is accepted for GPT-5.6 and GPT-6 models.

For GPT-5.6 and GPT-6 models, `minimal` is accepted as a compatibility alias and normalized to `low` before run
persistence and provider calls. This applies to chat, document workspace, replay inheritance, and
server defaults (including guests). Responses and events report the effective `low`; All other effort values remain unchanged.

Full contract: [run reasoning preferences](./26-run-reasoning-preferences.md).

## Temporary document workspace

With `MY_AGENTS_DOCUMENT_WORKSPACE_ENABLED=true`, approved registered accounts can attach temporary
files to a conversation, analyze them with GPT-5.6 Sol, and download certified outputs (`.xlsx`,
`.csv`, `.tsv`, `.docx`, `.pptx`, `.pdf`, `.md`, `.markdown`, `.html`, `.htm`). Guests are
ineligible, and every upload requires explicit consent to transfer the file to OpenAI. File bytes
are never stored in the Product DB; they remain only in expiring OpenAI `user_data` files and a
network-disabled hosted container.

Full contract: [OpenAI document workspace](./25-openai-document-workspace.md).

## LangGraph persistence and experimental memory

- PostgreSQL deployments construct PostgresStore and PostgresSaver as baseline LangGraph resources.
  Run `uv run python -m scripts.langgraph_persistence setup` before serving PostgreSQL traffic. The
  deployed container runs the same operator commands with
  `uv run --no-sync python -m scripts.langgraph_persistence <command>`.
- PostgresSaver lets ambiguous document-grounded runs return `202 waiting_for_input`, survive a
  process restart, and resume after an authorized document selection. SQLite keeps Product DB recall
  and a non-durable graph execution fallback.
- The shared LangGraph connection pool checks connections before checkpoint/Store work and replaces
  server-disconnected idle connections. This does not replay the graph or guarantee recovery from a
  connection lost mid-operation. No extra environment variable or migration is required.
- Store is a semantic projection of Product DB-governed memories. The per-user experimental setting
  controls consent and eligibility, while Product DB rows continue to enforce status, sensitivity,
  provenance, and source staleness. Verify zero-drift memory reconciliation before enabling
  experimental memory for users. The gated PostgreSQL checkpoint restart smoke passed against the
  local pgvector profile on 2026-08-17.
- Experimental memory currently recalls explicit memories and manually confirmed suggestions.
  Ordinary chat does not form memories automatically; the post-turn `memory_graph`
  extraction/update workflow remains planned.

See [LangGraph-native memory migration](./19-langgraph-native-memory-migration.md).

## Full-document retrieval

Full-document retrieval activates for explicit or clearly implied comprehensive requests, such as
“review the entire document” or “review this document without missing anything.” The RAG Agent
chooses between focused chunk search and a comprehensive document read; deterministic mode and
provider failures use the same contract's local fallback.

- `MY_AGENTS_FULL_DOCUMENT_MAX_CHARS=24000` controls complete one-call coverage. Larger documents
  currently provide only the first `MY_AGENTS_FULL_DOCUMENT_RANGE_CHARS=12000` characters and
  return `mode=partial`.
- Exact filenames resolve automatically. Ambiguous references return at most five ranked
  authorized candidates, accept up to two bounded filename refinements on the same run, and expose
  broad browsing only after those attempts are exhausted.
- The full-document graph bumps waiting-run compatibility to `general-assistant-checkpoint-v2`.
  Drain or cancel older waiting runs before rollout; an older graph version cannot resume and is
  failed safely by the version-mismatch path.

See [permission-aware RAG](./06-permission-aware-rag.md).

## Deployment notes

- Run admission is database-atomic. Apply Alembic `20260905_0034` before deploying; see
  [atomic run admission](./31-atomic-run-admission.md).
- Render pre-deploy steps: [Render migration and rollback notes](./14-render-migration-and-rollback-notes.md#production-pre-deploy-guardrail).
- Operational and migration commands: [scripts/README.md](../../../scripts/README.md).

## Local tooling

The VS Code `FastAPI: uvicorn main:app (local pgvector)` profile runs its pre-launch migration with
the interpreter selected by the Python extension, so it works even when a GUI-launched VS Code
process cannot find `uv` on `PATH`. Select this repository's `.venv` interpreter before using the
profile.

## Frontend API contracts

- Public `GET /auth/guest/policy` reports the deployed guest TTLs, usage limits, and
  code-delivery mode. UIs should display these values instead of hard-coding them.
- Run responses carry conservative answer-supported `citations`, separately disclosed
  `consulted_sources`, and human-readable document/knowledge-base citation metadata.
- HTTP and validation errors return a stable machine-readable `code` alongside the existing
  `detail`. UIs should localize from `code` and treat `detail` as diagnostic copy.
- `GET /conversations/{conversation_id}/runs/{run_id}/events` is a closed OpenAPI union
  discriminated by `event_type`. Persisted event types include `run_interrupted`, `run_resumed`, and
  redacted `full_document_read` metadata in addition to the existing run, retrieval, graph,
  workspace, answer, cancellation, and failure events.
- With checkpointer support enabled, run creation returns either `200 completed` or
  `202 waiting_for_input`. A waiting document-selection interaction is refresh-safe through the run
  detail/options endpoints and resumes through `/runs/{run_id}/resume` or `/resume/stream` without
  consuming another guest prompt. Resume SSE emits `run_resumed`, then real progress and answer
  deltas, including another `run_interrupted` after an unresolved refinement. Options include only
  user-controllable personal/group documents; ambient system knowledge is never selectable. New
  interactions use semantic `schema_version=2`; already-waiting V1 checkpoints remain resumable. See
  the [agent-to-frontend interaction contract](./27-agent-frontend-interaction-contract.md).
- Completed comprehensive-document runs add nullable `document_coverage` to sync, streamed, replay,
  and refreshed run responses. It reports `mode`, document metadata, `[start_offset, end_offset)`,
  and `total_chars`; it never exposes raw text or the internal continuation cursor. A partial answer
  also starts with a localized disclosure that it is not a complete-document review.
- `GET /capabilities/document-workspace` reports effective enablement, eligibility, accepted
  formats, limits, and retention. Attachments use
  `POST/GET/DELETE /conversations/{conversation_id}/attachments`; artifacts use
  `GET /conversations/{conversation_id}/artifacts` and their download URLs. A run's
  `attachment_ids` selects the files used for that execution.
- `GET /capabilities/reasoning` reports per-surface Pro support, the server-default effort, stable
  enums, and whether the current account may customize them. It intentionally omits raw provider
  model identifiers. Optional run/replay `reasoning_mode` and `reasoning_effort` values are
  persisted as effective run metadata and returned in responses and the `run_started` event.
- Completed runs may also return bounded `reasoning_summaries` for retrieval planning and answer
  synthesis. These model-authored explanations are nullable, stream separately through
  `reasoning_summary_delta`, persist as dedicated events for refresh/replay, and never replace the
  verified `agent_trace`, citations, or answer text.
- Ordinary OpenAI-backed answers use `ChatOpenAI.stream()`, so LangGraph emits real provider chunks
  as multiple `answer_delta` events. The provider aggregates the same chunks for final persistence
  and keeps reasoning-summary blocks out of reply text. Deterministic fallback and
  comprehensive-document disclosure paths may still buffer by design.
- Persisted event payloads and `agent_trace` expose only fields accepted by event- and
  stage-specific allowlist schemas. `answer_delta`, `run_completed`, and `run_error` are stream-only
  SSE events and are not members of the persisted-event union.
- Each non-skipped `agent_trace` step carries an optional versioned `operational_summary`: a closed
  semantic message key plus event-specific safe parameters. This application-verified channel is
  separate from model-authored `reasoning_summaries`. Unknown future summary versions are ignored
  without dropping the verified step.
- Conversation stream OpenAPI publishes the SSE-only `reasoning_summary_delta` payload through
  `text/event-stream.x-sse-events` on normal, resume, and replay routes. Deltas are not individually
  capped at 500 characters; the authoritative completed summary item is bounded before persistence.
- Async ingestion commits observable progress as `queued=0`, `claimed=1`, `chunking=15`,
  `embedding=45`, optional `indexing=70`, `entities=85`, `metadata=95`, and `completed=100`. These
  mark stages reached, not elapsed time.

The broader streaming contract is in [HTTP streaming and frontend contract](./09-http-streaming-frontend-contract.md).
