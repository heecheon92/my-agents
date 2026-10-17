# Recoverable answer generation

This guide describes the opt-in durable backend contract in the working implementation. It is not
evidence of a hosted migration, frontend release, or production enablement. Current verification
and release status belong to [implementation tracking](../implementation-tracking.md#recoverable-answer-generation).
The [design plan](../plan/durable-answer-generation.md) records the coordination rationale.

## Execution and delivery

The API admits a job and returns HTTP 202. A separately running answer worker claims it,
executes the existing graph, and saves displayable output before a subscriber receives it.
Closing an SSE connection detaches that subscriber; only explicit cancellation requests stop
the work. Multiple subscribers can follow the same run.

Product DB owns three distinct records: job ownership and run status; the bounded delivery
ledger and its recoverable snapshot; and the canonical completed message with citations.
The existing audit events remain allowlisted metadata. Answer text and permitted reasoning
summaries use the dedicated output tables. LangGraph checkpoints remain bounded execution
state under `run_id`, including waiting interactions; they are not conversation history.

The worker uses the existing LangGraph/ChatOpenAI and document-workspace execution paths.
No additional queue service or dependency is required. Answer model and reasoning preferences are pinned at
admission; execution revalidates account and source access.

## Setup and readiness

`MY_AGENTS_DURABLE_RUNS_ENABLED` defaults to `false`. Existing chat/regeneration use the legacy
flow and require no answer worker. To opt in, set the flag to `true` in both API and worker
environments before starting them. Local testing can enable this flag while Render keeps it
unset or false; each deployment has independent configuration.
The default preserves the existing single-service deployment without provisioning a hosted worker.
The separate worker is an opt-in operational prerequisite, not something the API starts automatically.

API and worker must use the same persistent `MY_AGENTS_DATABASE_URL`. In-memory SQLite cannot share jobs
between processes. File-backed SQLite is supported for local execution/output tests;
PostgreSQL is the production database and provides the existing cross-process LangGraph
checkpoint/Store infrastructure required for durable HITL resume.

Apply the Product DB migration and, on PostgreSQL, provision the existing LangGraph tables
before starting traffic:

```bash
uv run alembic upgrade head
# PostgreSQL only; use the same configured database and embedding settings as the app.
uv run python -m scripts.langgraph_persistence setup
```

Migration `20261017_0040` adds `durable_run_jobs`, `durable_run_outputs`,
`durable_run_frames`, and `durable_worker_heartbeats`, and extends active-run uniqueness to
include `queued`. Use the existing [local database and migration workflow](../../scripts/README.md)
for prerequisites and database selection. Apply migrations before accepting durable requests.

Run the API and worker as
separately supervised processes with the same model, database, and applicable workspace settings:

```bash
# API process
uv run uvicorn main:app --host 127.0.0.1 --port 8000
# Separate terminal/process
uv run python -m my_agents.answer_worker
```

After opting in, VS Code compounds in `.vscode/launch.json` launch API and worker together. Use
**Chat backend: API + answer worker (local pgvector)** for the local PostgreSQL profile;
the unsuffixed compound uses the configured database. The PostgreSQL compound runs the existing
database/migration task before launching both processes. A standalone terminal worker must
receive the same database overrides as the API: reading `.env` alone may select another database.

`--once` processes at most one queued job and exits; it is a local/operator check, not a
continuously available worker. `MY_AGENTS_RESPONSE_MODE=deterministic` supports credential-free
verification; normal OpenAI generation still needs the existing credentials.

`GET /health` exposes `frontend_config.durable_runs.enabled` (the configured feature flag) and
`ready` (the flag is on, the database supports background sessions, and a worker heartbeat is
newer than the lease duration). This is readiness evidence, not a deployment acceptance test. New async admission,
resume, and regeneration return HTTP 503 `durable_runs_unavailable` without readiness; exact
idempotent retries can still return the previously admitted receipt. Output reads, subscriptions,
and cancellation of existing durable runs do not depend on worker readiness. The frontend selects
legacy new-chat/regeneration when `enabled` is false or absent. Once enabled, missing worker
readiness produces an explicit error, never a silent retry through legacy generation.

| Setting | Default | Meaning |
| --- | --- | --- |
| `MY_AGENTS_DURABLE_RUNS_ENABLED` | `false` | Opt in to independent worker execution and new async admission |
| `MY_AGENTS_DURABLE_RUN_LEASE_SECONDS` | `60` | Ownership lifetime and heartbeat freshness window |
| `MY_AGENTS_DURABLE_RUN_POLL_SECONDS` | `0.25` | Idle worker and listener polling interval |
| `MY_AGENTS_DURABLE_RUN_MAX_FRAMES` | `2000` | Retained delivery-frame window and activity projection bound |
| `MY_AGENTS_DURABLE_RUN_QUEUE_TIMEOUT_SECONDS` | `300` | Maximum queue age before lost-work reconciliation |
| `MY_AGENTS_DURABLE_RUN_BATCH_MS` | `150` | Answer-text batching time threshold |

Text batches flush when the time threshold is observed on an incoming chunk, the buffered text
reaches approximately 4,096 characters, or a non-text event requires ordered delivery. This is
not a strict 150 ms timer: no provider chunk means no timer-driven flush. Measure real latency
before tuning. The aggregate partial reply has a fixed 2,000,000-character limit across phases.

## Admission, discovery, and cancellation

All paths below require the existing conversation authorization. Mutations retain CSRF policy.

| Operation | Route | Request/result |
| --- | --- | --- |
| Answer | `POST /conversations/{cid}/runs/async` | Existing run body with required UUID `client_request_id` |
| Resume | `POST /conversations/{cid}/runs/{rid}/resume/async` | `{client_request_id, answer: <semantic resume request>}` |
| Regenerate | `POST /conversations/{cid}/messages/{mid}/replay/async` | `{client_request_id, request: <ConversationReplayRequest>}` |
| Snapshot | `GET /conversations/{cid}/runs/{rid}/output` | `DurableOutput` for any status |
| Subscribe | `GET /conversations/{cid}/runs/{rid}/stream?after=N` | Committed SSE frames after cursor N |
| Stop | `POST /conversations/{cid}/runs/{rid}/cancel` | Existing cancellation response |

Each admission returns HTTP 202 `{run_id, conversation_id, client_request_id, status}`.
Operation keys are scoped by user and kind (`run`, `resume`, `replay`). The same key and input
return the same IDs with the **current** status, including after completion. Different input
or conversation under that key returns 409 `run_request_id_conflict`; other active work in the
conversation returns 409 `conversation_run_already_active`. A stale interaction under a new key
remains a conflict. Resume continues the logical run; regeneration creates a new run and keeps
success-only transcript replacement.

Run-list summaries and interrupted-run detail expose `execution_mode: "durable" | "legacy"`
(schema default `legacy` for compatibility). Use this per-run value when recovering or resuming;
worker readiness only determines whether new work can be admitted. Never attach a durable listener to a legacy run
or send a durable waiting run through legacy resume. Async resume rejects legacy runs with no
durable output; legacy resume rejects durable runs. A 404 from `/output` does not authorize a
replacement run or a silent retry through legacy generation.

Statuses are `queued`, `running`, `waiting_for_input`, `cancelling`, `completed`, `failed`,
and `cancelled`. Stop cancels queued/waiting work immediately. Running work enters `cancelling`
and stops cooperatively; a blocking provider call need not stop instantly. Completion and Stop
compete through guarded database transitions. Stop does not rewrite an already committed
completion. A cancelled run adds no completed assistant message, although its partial output
may remain for a stopped display.

## Snapshot and SSE recovery

`DurableOutput` contains `schema_version: 1`, run/conversation/request IDs, status, `cursor`,
`partial_reply`, `reasoning_summaries`, `activity_events`, `interaction`, `result`, and `error`.
`activity_events` use `{id, event, data}` with the same redacted activity payloads as live output.
`interaction` preserves the existing semantic union. `result` is the canonical completed
`ConversationRunResponse`; `error` is null or a safe `{code, detail}`.

The snapshot reads run status and the saved projection/cursor together. Replace local partial
state with the snapshot, then subscribe after its cursor. Delivery IDs are monotonic for the
logical run and independent of `answer_delta.sequence`; they appear in SSE `id:` lines, not
inside the JSON payload. Deduplicate text by delivery ID. Resume retains accumulated partial
answer text and cursor; interaction prompts are separate. The canonical completion replaces
partial display state.

The listener accepts `after` and `Last-Event-ID` as nonnegative integer cursors. With neither,
start at zero; when both are present, they must match. Malformed or future values return
400 `invalid_stream_cursor`. A cursor older than retained frames returns
410 `run_output_cursor_expired`: fetch a fresh snapshot and subscribe from its cursor.

The route's OpenAPI `x-sse-events` describes `answer_delta`, `reasoning_summary_delta`, live
`run_started`, `run_resumed`, graph/activity metadata, and these control events:

| Event | Payload and client action |
| --- | --- |
| `run_completed` | Full `ConversationRunResponse`, identical to snapshot `result`; replace partial display, close |
| `run_interrupted` | Full `ConversationRunInterruptedResponse`, including `status: "waiting_for_input"` and interaction; stop reconnecting until successful resume |
| `run_failed` | `{run_id, conversation_id, status: "failed", code, detail}`; safe failure display, close |
| `run_cancelled` | `{run_id, conversation_id, status: "cancelled", partial_reply_persisted: false}`; label any remaining partial text as stopped, close |

Completion is delivered only after the canonical transcript commit. If a process dies between
that commit and saving its control frame, terminal reconciliation reconstructs the frame from
the saved run. The listener replays remaining frames for ended/waiting runs, emits their current
control event, and closes. Even an already acknowledged control event can repeat as the close
signal; honor it without appending duplicate text. Old interruption frames from earlier phases
are not the current close signal after resume. A waiting snapshot also stops reconnecting.

An unexpected close without a current control event means reconnect, not re-execute. SSE comment
heartbeats are sent approximately every 15 seconds. Each polling read rechecks access; revoked
access detaches that listener without making it the owner of worker cancellation.

Durable regeneration commits replacement history and old-suffix pruning in one transaction.
Another run admitted after completion cannot be removed by delayed replay cleanup. Pruning failures
roll back the replacement and preserve the old transcript. Delivery follows the canonical run state
if response construction fails after a successful commit.

## Failure, retention, and rollout limits

Jobs are claimed atomically, renewed with leases, and fenced against writes from expired owners.
Expired running leases or queued jobs older than the queue timeout are reconciled to `failed`
with stable `worker_lost`, unless an explicit cancellation already requires `cancelled`.
Reconciliation runs from the worker and durable read paths; it is not an independent scheduler.
There is no automatic retry or seamless continuation of an interrupted provider call. Unknown
failure codes should render safe generic copy. `worker_lost` is an output error code, not an
HTTP admission error enum value.

Old delivery frames are pruned by count; the logical snapshot, final/control state, and
idempotency records remain until run/conversation erasure. There is no time-based output expiry
in this version. Partial text and output summaries inherit conversation authorization and erasure
rules, including guest cleanup. `partial_reply_persisted: false` specifically means no completed
transcript message; it does not mean all partial output was deleted. This content is distinct
from the metadata-only audit ledger.

The frontend must support the new BFF routes, validated `after` forwarding, `Last-Event-ID`,
unbuffered delivery, and per-run recovery. A locally served `/openapi.json` is enough for development
model generation. Production enablement requires the deployed contract, database migration,
supervised worker, matching frontend, readiness, and a disconnect/reopen acceptance check.
Pause incoming admissions and drain or cancel existing work before stopping workers. Migration downgrade refuses pending jobs and active durable runs, including waiting interactions; a completed drain does not preserve
the output tables when downgrading.

Useful focused checks are in `tests/test_durable_runs_api.py`, `tests/test_durable_store.py`,
and `tests/test_durable_migration.py`. Their scope includes admission retries, competing claims,
disconnect versus Stop, snapshot/replay, resume, erasure, lease loss, and migration guards.
Read current test results in tracking; this guide does not assert a hosted release or a passing
test count.
