---
created: 2026-10-17
updated: 2026-10-17
status: active
topics:
  - durable-execution
  - streaming
  - concurrency
  - persistence
related_code:
  - my_agents/answer_worker.py
  - my_agents/conversations/durable_store.py
  - my_agents/conversations/durable_runtime.py
  - my_agents/api/conversations/endpoints/durable.py
  - tests/test_durable_runs_api.py
  - tests/test_durable_store.py
---

# Why an answer job must outlive its viewer

## Symptom and root cause

A user can lose a streaming connection while the model is still answering. In the legacy path,
the HTTP generator owns execution, and closing it can cancel the run before a completed answer
is saved. Refreshing after completion works; refreshing during generation is a different case.
The opt-in durable path separates admission, worker execution, and subscription. See the
[current contract](../../product-chat-service/42-durable-answer-generation.md) for exact API/setup
and [tracking](../../implementation-tracking.md#recoverable-answer-generation) for validation status.

## Three independent lifetimes

1. The admission request lasts until the database commits a run/job and returns its receipt.
2. The job lasts until completion, failure, cancellation, or a semantic input pause. A worker
   holds a renewable ownership lease while executing it.
3. A subscription lasts only while a viewer is connected. Two tabs can subscribe to one run;
   neither tab owns generation.

Reconnecting means reading later committed output. Regenerating means creating a new run and
calling the model again. Stop is an explicit state transition, not an interpretation of a
socket closing.

Saving the whole model answer before displaying it would preserve a completed result but delay
the first visible words. Saving first inside the same HTTP generator would still tie execution
to the viewer. The chosen design saves incremental batches in an independent worker, then lets
the API deliver those saved batches. A database queue fits the existing stack without another
queue dependency.

## Which record answers which question?

| Record | Question it answers |
| --- | --- |
| Job and lease | Which worker may execute and commit this operation? |
| Run state | Is the answer queued, running, waiting, stopping, or finished? |
| Output ledger and snapshot | What can the viewer safely display through delivery cursor N? |
| Completed message and citations | What is the authoritative answer in conversation history? |
| LangGraph checkpoint | What bounded execution state is needed to resume this run's interaction? |
| Audit events | Which allowed metadata steps happened? |

A partial answer is not a completed transcript message. A checkpoint is not conversation history
or user memory consent. Keeping these responsibilities separate lets a cancelled partial remain
visible as stopped without pretending it is a final answer.

## Why snapshot and cursor must agree

Suppose a snapshot contains `Hello` through cursor 12. The client subscribes after 12 and receives
` world` at 13. If a broken snapshot returned cursor 13 while still containing only `Hello`, the
client would skip the missing suffix. Reading projection and cursor together, and writing each
batch with its projection update in one transaction, prevents that gap.

The delivery cursor covers answer text, summaries, activities, and control frames. It differs
from an answer delta's sequence number and does not reset after an interaction. A repeated
completion/interruption frame can still be necessary to close the subscription even when its
ID was acknowledged earlier; text deduplication must not discard the close signal.

## Races that need behavioral tests

- **Ambiguous admission:** the server commits but the receipt is lost. Retrying the same key/input
  must recover the same run, not create another prompt or model call.
- **Two workers:** both see queued work. Only the atomic claimant may execute; an expired owner
  must fail its next guarded write, even if its model response eventually arrives.
- **Stop versus completion:** both may happen almost together. Guarded transitions preserve one
  terminal outcome and prevent a completed assistant message after cancellation wins.
- **Commit versus delivery:** final transcript commit can precede the saved completion frame.
  Reconciliation must reconstruct the close signal without generating another answer.
- **Disconnect versus Stop:** close the actual listener while a deterministic producer is paused,
  then release it and verify one completed message. Repeat with explicit Stop and verify cancellation.
- **History pruning:** an expired cursor must request a fresh snapshot; trimming old frames must
  preserve the current output and control state.

The focused store/API tests exercise these scenarios. File-backed SQLite is useful for local
tests, but PostgreSQL concurrency and shared checkpoint behavior require their own verification.
Neither reading source nor seeing a health flag proves the deployed frontend can reconnect.

## Limits and next experiments

The batching threshold is checked when provider chunks arrive; it is not a strict timer. Observe
first-word delay and database writes under realistic streams before changing it. Worker loss is
reported as `worker_lost`, without automatically repeating an uncertain provider call. Count-bound
frame pruning does not imply time-based deletion of snapshots; erasure owns that lifecycle.

Try tracing one answer through admission, one text batch, a browser disconnect, and completion.
For every step, identify the database owner and explain why a second subscriber cannot duplicate
execution. Then trace the same run through a waiting interaction and a resume key retry.

## Regeneration must finish atomically

A review found that saving the replacement answer and only then pruning the old transcript left
an admission gap. Another tab could start a run after the completed status became visible, and
that new run could be included in the later suffix deletion. The durable path now prunes within
the completion transaction and skips the generator's second prune. An injected pruning failure
rolls back both completion and deletion before legacy error handling can commit anything else.
Regression tests pause after completion, admit a new run, and prove it survives; a failure test
proves the original transcript and outputs remain intact. A late response-serialization error
must also deliver the canonical saved completion rather than a contradictory failure event.

## A local browser verification trap

During verification, the frontend repeatedly returned to session restoration and browser input
lost focus. Both a pre-existing browser test and the new recovery tests failed on that dev server.
Opus traced full reloads and aborted auth requests to launching the dev server with a loopback IP
hostname while opening it through `localhost`. Matching the server hostname to the existing
frontend test/proxy origin stopped the reload loop. No application fix or security-policy weakening
was needed. Control tests, recovery tests, and independent UI login/reload then passed.

Treat a successful HTTP response or real-proxy script as separate evidence from browser hydration.
Do not attribute an unstable page solely to the browser-control tool until console/network evidence
and a control scenario isolate it.

Worker readiness reports availability, while `MY_AGENTS_DURABLE_RUNS_ENABLED` controls feature
selection. The final owner decision restores default-off operation to avoid requiring an additional
paid Render worker service. Disabled new-chat/regeneration use legacy execution. When enabled,
an unavailable worker returns an explicit error. Existing runs retain their own resume contract.

## Local startup failure: API and worker configuration differ

Symptom: new chat returned `503 durable_runs_unavailable` after removing the feature switch.
The API was running through the VS Code local PostgreSQL profile, whose environment overrides
selected port 5433. No worker heartbeat existed there. A plain terminal loaded a different
default database; merely launching the worker without matching settings would not fix the API.
The local PostgreSQL schema was already at `20261017_0040`, so no migration repair was needed.

Starting the worker with the same profile changed the actual API health from `ready: false` to
`ready: true`. VS Code compound configurations now launch both processes with identical overrides.
Validation checked JSON, profile equality, migration revision, and the live API readiness result.
These compounds are for explicit opt-in use; ordinary API-only startup remains the default.
The standalone worker still needs supervision in a hosted deployment; this local fix does not
provision or validate a Render worker.

## Revision history

- 2026-10-17: Recorded the separation of request, job, and subscriber lifetimes; projection/cursor
  consistency; cancellation and ownership races; and meaningful verification scenarios.

- 2026-10-17: Recorded the missing local worker, database-profile mismatch, and compound startup fix.
- 2026-10-17: Restored the default-off environment flag to preserve the existing single-service deployment; distinguished feature enablement from worker readiness.
