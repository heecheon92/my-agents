---
created: 2026-06-06
updated: 2026-06-06
status: active
topics:
  - postgres
  - production-debugging
  - guest-access
related_code:
  - my_agents/persistence/database.py
  - my_agents/auth/service.py
  - my_agents/api/auth.py
---

# Production guest login stale Postgres SSL connection

## Symptom

A production guest login request returned `500 Internal Server Error` while querying `guest_access_codes`:

```text
sqlalchemy.exc.OperationalError: psycopg.OperationalError
consuming input failed: SSL connection has been closed unexpectedly
```

The failure happened during `AuthService.redeem_guest_access_code()` when SQLAlchemy reused a pooled Postgres connection that the remote database/proxy had already closed.

## Root cause

The SQLAlchemy engine used the default pool behavior for Postgres. In hosted environments, Neon/Render/proxies may close idle SSL connections. Without `pool_pre_ping`, SQLAlchemy can hand an already-closed connection to a request, and the first query fails before application auth logic can run.

## Fix

The database boundary now enables Postgres-only engine hardening:

- `pool_pre_ping=True` checks a pooled connection before checkout and replaces it when stale.
- `pool_recycle=300` avoids keeping old idle connections indefinitely.
- In-memory SQLite still uses `StaticPool` for deterministic tests.

## Rejected fixes

- Catching this only in `guest_login`: too narrow; the same stale pooled connection can affect any endpoint using the request-scoped database session.
- Disabling pooling globally: unnecessary and likely slower for normal hosted traffic.
- Adding app-level keepalive pings: the roadmap already treats keepalive/warmer code as the wrong fix for hosted infrastructure behavior.

## Verification

Focused tests cover the engine kwargs for Postgres and in-memory SQLite. The fix should be deployed before retrying the production guest-code login path.

## Follow-up risks

`pool_pre_ping` fixes stale connections at checkout. It cannot prevent a database or network outage that drops a connection in the middle of an active query. Those should remain visible as infrastructure incidents rather than be hidden by broad retries around mutating auth flows.

## Revision history

- 2026-06-06: Created learning log for `Production guest login stale Postgres SSL connection`.
