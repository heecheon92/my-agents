# Backend maintenance integration

- Status: Completed integration; later connection-pool incident tracked separately.
- Completed: 2026-09-05.
- Integration/release: `7a450cc`; subsequent pool hotfix `e62d45a`.
- Canonical status: [implementation tracking](../implementation-tracking.md#shipped-and-completed-index).
- Current contracts: [atomic admission](../product-chat-service/en/31-atomic-run-admission.md),
  [answer finalization](../product-chat-service/en/32-answer-finalization.md),
  [document resolution](../product-chat-service/en/33-document-resolution-helpers.md).

## Delivered scope

The three slices were combined on `refactor/backend-maintenance` for one owner test pass,
then integrated into develop and main; the integration is no longer pending.

- Atomic run admission enforces one active run per conversation. Migration `20260905_0034`
  rejects duplicate active conversations without silently discarding runs.
- Shared answer preparation preserves public behavior, event ordering, and transaction ownership.
- Pure document-resolution helpers preserve RetrievalService imports and ranking behavior.

## Acceptance evidence

Historical individual suites: admission 583 passed/12 skipped; finalization 574/3;
resolution 572/3. Combined suite: 585 passed/12 gated skips/11 dependency warnings, with
Ruff lint/format clean across 258 files. Eighteen isolated PostgreSQL/SQLite admission and
migration cases passed; OpenAPI matched the validated branches and temporary test DBs were removed.
The owner reported no problems during the combined manual test pass.

## Limits and follow-up

The later production checkpoint connection failure was not caught by those fresh-connection
tests. Hotfix e62d45a added checkout health checks and local stale-connection regressions;
the owner subsequently reported successful authenticated chat. See the
[incident note](../learning/project-notes/langgraph-stale-connections.md). Longer-idle production
verification remains distinct from immediate recovery. These records do not certify all
product flows or erase the remaining operational, queue, and security work.
