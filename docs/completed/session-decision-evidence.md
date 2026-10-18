# Session decision evidence

Completed: 2026-10-18
Status: implemented and verified in the repository; hosted rollout not verified.

- Backend-only private answer-input records and bounded prior-turn recall are implemented. No frontend, dependency, worker, or public schema change.
- Capture supplied document excerpts after final packing; keep recorded inputs, explicit
  claims/reported routing reasons, and inferred rationale distinct. Same-conversation ownership,
  current source authorization, failed-run exclusion, replay cutoff, and deletion apply.
- Migration `20261018_0043` must be applied before restarting existing API/worker processes.
  No live application database or running service has been changed for this work.
- Verification (2026-10-18): `uv run pytest -q` — **1,197 passed, 42 skipped, 12 warnings**;
  `uv run ruff check . --no-cache` and `uv run ruff format --check .` passed (332 files).
  The 25 new offline scenarios cover exact packed excerpts, later corrections, source revocation
  including derived follow-ups, malformed records, replay boundaries, sync/SSE/worker integration,
  deletion, and transaction preservation. Warnings remain existing dependency deprecations;
  optional PostgreSQL integration was not configured. Real-provider fidelity and arbitrary
  older-turn lookup beyond the bounded 64-turn selection remain unverified/deferred.
- Current behavior: [continuity contract](../product-chat-service/36-conversation-continuity.md#session-evidence-for-earlier-decisions).
  Investigation: [learning note](../learning/project-notes/session-evidence-and-decision-continuity.md).
