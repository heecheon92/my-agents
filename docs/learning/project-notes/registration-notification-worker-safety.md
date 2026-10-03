---
created: 2026-10-03
updated: 2026-10-03
status: implemented
topics: [asyncio, sqlite, transactions, shutdown]
related_code:
  - my_agents/auth/notifications.py
  - my_agents/persistence/database.py
  - tests/test_registration_notifications.py
---

# Registration notification worker safety

Two review reproductions exposed why passing ordinary delivery tests was insufficient.

A request flushed a new user into in-memory SQLite, then an empty worker sweep closed
another session. Both sessions used the single StaticPool connection, so the worker's
rollback removed the request's writes. Another Session object does not guarantee another
transaction/connection. The worker now requires file-backed SQLite or PostgreSQL; memory
SQLite remains available for synchronous, isolated tests. Replacing StaticPool with a
normal pool would create separate in-memory databases rather than solve persistence.

During shutdown, cancelling the coroutine awaiting `asyncio.to_thread()` did not stop the
thread. A blocked first delivery could finish and start the remaining batch after shutdown.
The worker now shields the thread task, sets a threading Event on cancellation, and waits
for the current send. The batch checks the stop signal before claiming and before sending
another item. Shortening the poll interval or cancelling the coroutine alone does not stop
a synchronous transport call.

Regression tests preserve a flushed request across a memory-worker attempt and block the
first send while cancelling the worker, proving the second job remains unclaimed. Full
validation: 845 passed, 14 skipped, 12 dependency deprecation warnings; Ruff checks passed.
SMTP/Resend inbox delivery and PostgreSQL concurrency remain separate verification tasks.

## Revision history

- 2026-10-03: Recorded the two reproduced defects, fixes, and regression evidence.
