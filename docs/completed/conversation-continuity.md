# Conversation continuity and temporary-file recall

- Status: shipped repository implementation; deployment is a separate operation.
- Completed: 2026-09-30.
- Current behavior: [English contract](../product-chat-service/36-conversation-continuity.md),
  [Korean contract](../product-chat-service/36-conversation-continuity.ko.md).
- Design history: [original proposal and pre-implementation diagnosis](./conversation-continuity-design-history.md).

## Delivered scope

One shared context policy replaces the duplicate six-message truncation. Recent messages remain
verbatim under measured text budgets; older prefixes have source-linked, versioned summaries with
bounded generation, fallback and invalidation. Persisted/SSE compaction activity is separate from
answer text and reasoning summaries. Product DB remains authoritative; checkpointers stay run-scoped
and existing long-term-memory consent is unchanged.

Summarization defaults to GPT-6 Luna through the environment and a registered-user Settings
preference. The exposed catalog remains a subset of the six API-supported models. User-selected
ordinary models and the separately configured workspace model are captured for each run.

Explicit attachments belong to submitted user messages. The frontend clears the submitted selection
on admission and retains its own queue snapshot. Optional UUID request correlation resolves lost
acknowledgements without guessing from identical text or automatically resending. Registered-user
follow-ups can use retained file observations or reopen authorized originals. Ambiguous references
use V2 attachment selection, including original/notes modes, refresh-safe run detail and resume.
Guests receive text continuity, without workspace file storage, notes or recall.

New original files expire at OpenAI seven days after upload; existing expiry is preserved. Unsubmitted
uploads become unavailable after 24 hours. Compact notes remain with the conversation after normal
expiry. Explicit removal clears notes/derived context; conversation deletion also removes its
transcript. A durable leased cleanup outbox preserves provider targets for erasure retries. Input
copies are limited to the selected files before workspace reuse, and pending erasure blocks reuse.
Original file bytes are never persisted in Product DB.

## Verification and fixes

- Backend full suite: 786 passed, 13 skipped, 12 deprecation warnings. Final affected regression
  checks: 48 passed, 1 skipped. Ruff lint/format and whitespace checks passed.
- Frontend: 425 unit tests; final browser suite 229 passed, 2 skipped, 2 confirmed pre-existing
  failures (whole-conversation file drop and transcript autoscroll). Lint, types and build passed.
- Hosted OpenAPI comparison and real BFF/UI checks with an offline provider adapter verified file
  admission/clearing, request correlation, original/notes selection, refresh recovery, resume,
  compaction activity and guest locks. Verification servers were stopped.
- Owner reported manually testing the feature in the browser and approved publication. This report
  does not identify specific formats, models, workloads or measured summary fidelity.
- Regression fixes cover transaction-failure terminal recovery, interrupts before RAG preparation,
  expiry cleanup after metadata listing, and waiting attachment-selection detail refresh. The apparent
  CSV-only failure came from duplicate IDs in the verification adapter; it also exposed a real ORM
  failure-recovery issue, which is tested independently of that adapter.

## Rollout and remaining limits

Apply migration `20260930_0036` before starting existing databases. Complete or cancel older waiting
runs before deploying graph `general-assistant-checkpoint-v3`. Repository publication is not evidence
that a database migration or hosted deployment occurred.

Representative real-provider summary fidelity, cost and latency measurements remain follow-up work.
The owner browser check supplements automated/stand-in evidence without establishing those metrics.
Provider outages can delay physical cleanup after application access is revoked. Existing request-bound
SSE execution and the absence of a persisted `run_completed` activity event remain separate limitations.
Generated-file durability and attachment-free artifact generation are outside this implementation.
