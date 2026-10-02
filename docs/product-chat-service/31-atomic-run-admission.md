# Atomic conversation run admission

Normal and replay requests share one admission operation. New user messages, the run and its
initial events commit together; replay reuses its existing prompt. A database partial unique index
allows one running, waiting_for_input or cancelling run per conversation. Competing starts receive
the existing 409 conversation_run_already_active response, with no orphan prompt or provider call.
Both streaming endpoints admit the run before sending HTTP headers. Stream event order is unchanged.
Authorization, quota prechecks and stale-run cleanup still precede admission.

Apply Alembic revision 20260905_0034 before deploying this code. The migration refuses duplicate
active rows rather than choosing a winner. Inspect them with:

```sql
SELECT conversation_id, count(*)
FROM agent_runs
WHERE status IN ('running', 'waiting_for_input', 'cancelling')
GROUP BY conversation_id HAVING count(*) > 1;
```

Resolve duplicates deliberately through normal run cancellation/operational review and retry.
Drain traffic during rollout so old workers cannot create competing rows between preflight and
index creation. Offline SQL relies on the unique-index DDL to reject duplicates. Downgrade removes
only the index; it does not delete runs. Ordinary database errors are not translated into 409.

This change protects admission within a conversation. Cross-conversation guest quota races,
background execution, and atomic replay pruning are separate concerns. A process lost immediately
after admission can leave an active run for the existing stale-run cleanup policy.

Tests exercise independent SQLite/PostgreSQL connections, normal/replay competition, all active
states, terminal-state release, rollback, migration refusal/downgrade and pre-SSE HTTP rejection.
PostgreSQL tests use a unique schema with explicit schema translation, never search-path fallback
to application tables. Runtime state, ranking and API response schemas are unchanged.
