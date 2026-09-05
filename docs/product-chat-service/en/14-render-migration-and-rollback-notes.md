# Render migration and rollback notes

## Purpose

This document preserves the deployment decisions made while stabilizing the backend on
Render. The goal is to keep the codebase portable: moving away from Render should mostly
mean changing environment variables and tuning deploy diagnostics, not rewriting auth,
email, or database logic.

## Production pre-deploy guardrail

### Confirmed configuration — 2026-09-05

The owner confirmed this Render log during the release:

```text
Starting pre-deploy: uv run --no-sync alembic upgrade head
```

The corresponding **Pre-Deploy Command** is:

```sh
uv run --no-sync alembic upgrade head
```

This is Render service configuration, not a repository startup script. The repository's
Dockerfile CMD starts Uvicorn; it does not run Alembic. Record a later dashboard change here
only after it is confirmed, rather than treating a proposed command as already configured.

Render runs pre-deploy after the image build and before the new service starts, on a separate
instance. A failed command prevents the new deployment from proceeding, but does **not** undo
database changes that an earlier command already committed. See
[Render deploy steps](https://render.com/docs/deploys#deploy-steps).

### Proposed stronger command — not yet configured

The current command covers Product DB migrations only. LangGraph owns a separate migration
history, so the recommended replacement for **Pre-Deploy Command** is:

```sh
uv run --no-sync alembic upgrade head &&
uv run --no-sync python -m scripts.langgraph_persistence setup &&
uv run --no-sync python -m scripts.langgraph_persistence status &&
uv run --no-sync python -m scripts.langgraph_persistence reconcile-memory
```

Use `&&`, not independent commands separated by `;`, so any nonzero exit stops the chain.
Do not append Uvicorn to this command: Render starts the service afterward. The candidate image
must include `scripts/`, as the current Dockerfile does. Configuring this recommendation in
Render is a separate operational action; documenting it does not change the deployed setting.

Before configuring or executing it:

- Confirm the exact production branch/database, recovery point, and effective embedding settings.
  Use a direct PostgreSQL connection for the migration/setup job; do not assume the application's
  pooled connection is suitable for every framework migration. Keep credentials out of command
  text and Git, and preserve the intended runtime connection configuration. The separate-instance
  job's connection selection must be arranged securely by the operator.
- `setup` applies framework migrations and is idempotent. It is not a memory extraction job.
  Existing Store vector dimensions still need to match the effective embedding provider;
  setup does not automatically resize an existing mismatched vector column.
- `status` verifies the presence of seven required tables. It is **not** a complete schema,
  vector-dimension, checkpoint-read/write, or application-readiness validator.
- `reconcile-memory` reports Product DB/Store projection drift without repairing it. Drift exits
  nonzero and deliberately blocks this strict gate, even if ordinary chat would otherwise work.
  Investigate the drift; do not add `--apply`, delete memory rows, or prune checkpoints to make
  deployment pass. Repair is an explicit decision and may incur embedding cost.

On 2026-09-05, the separate LangGraph setup was performed during the approved DB migration.
That one-time completion must not be mistaken for automatic coverage by the currently configured
Alembic-only command. No new environment variable is introduced by the recommended sequence.

### Before deploy: prove stale-connection recovery in an isolated database

Run the existing regression tests against a **fresh disposable local PostgreSQL database**
with pgvector. `MY_AGENTS_TEST_DATABASE_URL` is an existing test setting, not a new production
variable. Never point it at production, shared application data, or a tunnel to a hosted DB.

```sh
: "${MY_AGENTS_TEST_DATABASE_URL:?Set a disposable local PostgreSQL test database}"
uv run --no-sync pytest -q tests/test_langgraph_persistence.py
```

At the current test revision, acceptance is **4 passed, 0 skipped**: pool wiring, stale
checkpoint read, stale Store read, and restart/resume. Inspect the summary, not just exit code:
pytest can exit successfully while integration tests skip. The fault-injection cases reject
non-loopback hosts, but a loopback address alone does not prove a database is disposable.

These tests terminate a test-owned idle connection to exercise the recovery path. Never run
them in Render's production pre-deploy job. Run them in isolated local/release CI verification
before publishing the candidate; a failure or skip must fail that release gate. The tests exist,
but no repository CI workflow currently enforces this gate automatically. Adding this prose
does not make CI enforcement implemented, and the runtime image does not include dev test tools.

### Runtime and post-deploy protection remain necessary

Pre-deploy uses different connections from the running service and cannot keep those later
connections alive. The shared LangGraph pool's checkout health check replaces dead idle
connections within its existing acquisition timeout. It does not replay the graph or recover
every disconnect that occurs during an operation; genuine database/network outages remain
possible. Keep failures explicit rather than adding an unrestricted whole-run retry.

After rollout, verify an authenticated streamed answer completes, then repeat after an idle
period when practical. `/health` returning 200, an authenticated route returning 401 without a
session, and SSE's initial HTTP 200 do not exercise or prove successful checkpoint work.
The [incident note](../../learning/project-notes/langgraph-stale-connections.md) records the
failure, fix, local red/green evidence, owner-confirmed recovery, and remaining verification gap.

## Current Render hardware snapshot

Recorded on 2026-07-24 from the owner's active Render configuration:

| Setting | Current value |
| --- | --- |
| Render plan | Hobby (free) |
| Service instance type | Standard (paid) |
| CPU | 1 CPU |
| Memory | 2 GB RAM |

The Render plan and the service instance type are separate settings: the account remains on
the free Hobby plan while the backend service uses a paid Standard instance. References to
the Render free runtime below describe earlier deployment incidents and should not be read as
the current hardware allocation.

## Current Render-specific findings

- The earlier Render free web service had limited memory. Signup previously stopped during Argon2
  password hashing when the app used the `argon2-cffi` library defaults.
- Render free web services blocked outbound SMTP ports such as `25`, `465`, and `587` during
  the earlier deployment incident.
  Resend SMTP on port `587` timed out after the user row was already committed.
- The backend itself was correctly bound to `0.0.0.0:${PORT}` and Render reached the app.
  Port binding was not the signup failure.
- Neon connectivity was confirmed from Render through redacted runtime diagnostics.

## Portable decisions to keep

These are not Render-only hacks. Keep them unless there is a specific reason to change the
product behavior.

### Configurable Argon2 password hashing

Password hashing cost now uses explicit settings:

```env
MY_AGENTS_AUTH_PASSWORD_HASH_TIME_COST=2
MY_AGENTS_AUTH_PASSWORD_HASH_MEMORY_COST_KIB=19456
MY_AGENTS_AUTH_PASSWORD_HASH_PARALLELISM=1
```

Rationale:

- Password hashing should be tuned per runtime size.
- The current defaults are suitable for small demo containers.
- On a larger host, raise memory/cost only after measuring signup/login latency and memory
  headroom.

When migrating to a larger host, consider testing:

```env
MY_AGENTS_AUTH_PASSWORD_HASH_TIME_COST=2
MY_AGENTS_AUTH_PASSWORD_HASH_MEMORY_COST_KIB=32768
MY_AGENTS_AUTH_PASSWORD_HASH_PARALLELISM=1
```

Avoid increasing all three knobs at once. Measure after each change.

### Email sender boundary

Auth email remains behind `AuthEmailSender`. Supported modes:

```env
MY_AGENTS_AUTH_EMAIL_MODE=local       # local/dev only
MY_AGENTS_AUTH_EMAIL_MODE=smtp        # generic SMTP relay
MY_AGENTS_AUTH_EMAIL_MODE=resend_http # Resend HTTPS API
```

For Render free, use Resend HTTP:

```env
MY_AGENTS_AUTH_EMAIL_MODE=resend_http
MY_AGENTS_AUTH_FROM_EMAIL=<verified-resend-sender>
RESEND_API_KEY=<resend-api-key>
MY_AGENTS_RESEND_API_URL=https://api.resend.com/emails
```

For a host that allows SMTP, SMTP remains available:

```env
MY_AGENTS_AUTH_EMAIL_MODE=smtp
MY_AGENTS_AUTH_FROM_EMAIL=<verified-sender>
MY_AGENTS_AUTH_SMTP_HOST=<smtp-host>
MY_AGENTS_AUTH_SMTP_PORT=587
MY_AGENTS_AUTH_SMTP_USERNAME=<smtp-user>
MY_AGENTS_AUTH_SMTP_PASSWORD=<smtp-password-or-provider-api-key>
MY_AGENTS_AUTH_SMTP_USE_STARTTLS=true
```

For Resend specifically, the SMTP password and HTTP API key can be the same `re_...` secret,
but keep the environment variable names transport-specific.

## Deployment diagnostics to review after hosted signup is stable

The following are redacted deployment-debug surfaces. They are intentionally permanent
operational diagnostics, but should remain non-secret and low-noise:

- `my_agents/diagnostics.py`
- `DEPLOY_DIAG` request middleware logs
- signup step-by-step diagnostic logs
- password hash timing logs
- database session lifecycle diagnostic logs
- SMTP/Resend deploy diagnostics beyond normal operational logs

Recommended review approach:

1. Confirm hosted signup completes and verification email is delivered.
2. Keep `DEPLOY_DIAG` redacted; tune only specific noisy call sites if needed.
3. Keep the portable Argon2 settings and the `resend_http` sender.
4. Run auth email/settings/auth API tests before pushing.

## Migration checklist away from Render

When moving to Hostinger, Fly.io, Railway, ECS, a VPS, or another host:

1. **Runtime port**
   - Keep container command using `--host 0.0.0.0 --port ${PORT:-8000}`.
   - Set `PORT` only if the new host requires a specific port.

2. **Database**
   - Keep `MY_AGENTS_DATABASE_URL` pointed at Neon unless intentionally migrating DBs.
   - Keep `MY_AGENTS_AUTO_CREATE_TABLES=false` for hosted Postgres.
   - Run Alembic migrations against the approved production DB.

3. **Frontend/backend origins**
   - Update `MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL` to the active frontend URL.
   - Update `MY_AGENTS_CORS_ALLOWED_ORIGINS` to exact HTTPS frontend origins.
   - Keep `MY_AGENTS_SESSION_COOKIE_SECURE=true` for HTTPS.

4. **Email transport**
   - If the new host blocks SMTP, keep `resend_http`.
   - If the new host allows SMTP and provider portability matters, switch to `smtp`.
   - Verify the sender identity/domain in the provider before public signup.

5. **Password hashing**
   - Start with current defaults.
   - Raise Argon2 settings only after a hosted smoke test confirms latency and memory are safe.

6. **Resource-heavy options**
   - `MY_AGENTS_RERANKER_MODE=cross_encoder` loads ML dependencies and can be memory-heavy.
   - Lower `MY_AGENTS_RERANKER_TOP_K` from the default `40` if cross-encoder scoring is useful but the candidate window is too expensive.
   - On small hosts, switch to `MY_AGENTS_RERANKER_MODE=deterministic` if startup or memory is unstable.

7. **Document ingestion**
   - Hosted demos should set `MY_AGENTS_INGESTION_EXECUTION_MODE=external_worker`.
   - Run a separate worker process with `uv run python -m my_agents.ingestion_worker`.
   - Keep the web process responsible for upload/status/chat only; parsing, embeddings, metadata, and indexing belong in the worker.

8. **Diagnostics cleanup**
   - Keep deploy diagnostics redacted before treating the deployment as stable.
   - Keep operational logs and deploy diagnostics free of secrets and raw emails.

## Rollback recipes

### Database revision and image compatibility

An image whose Alembic scripts do not contain the database's current revision cannot run its own
`upgrade head` against that database. Check the chosen deployment/rollback method and which
migration runner it actually executes; do not assume an old image plus the pre-deploy command
is a viable rollback. A recovery branch preserves a data recovery option, not an automatic
application rollback, and restoring it can discard newer writes.

For the September 2026 release, `13607ae` predates revision0034. Both `7a450cc` and hotfix
`e62d45a` know revision0034, but returning to `7a450cc` reintroduces the unchecked-pool bug.
Distinguish migration compatibility from bug reintroduction; rollback is not universally
"forward-only". Do not automatically downgrade schema when rolling back application code.

### Signup fails during email delivery

Use Resend HTTP on SMTP-restricted hosts:

```env
MY_AGENTS_AUTH_EMAIL_MODE=resend_http
RESEND_API_KEY=<resend-api-key>
MY_AGENTS_AUTH_FROM_EMAIL=<verified-sender>
```

If public signup should be paused while preserving existing users:

```env
MY_AGENTS_AUTH_SIGNUP_ENABLED=false
```

### Signup or login is slow / memory constrained

Lower Argon2 cost temporarily:

```env
MY_AGENTS_AUTH_PASSWORD_HASH_TIME_COST=1
MY_AGENTS_AUTH_PASSWORD_HASH_MEMORY_COST_KIB=8192
MY_AGENTS_AUTH_PASSWORD_HASH_PARALLELISM=1
```

This is a demo-stability rollback, not a preferred long-term security posture.

### Cross-encoder causes memory or startup issues

Rollback to deterministic reranking:

```env
MY_AGENTS_RERANKER_MODE=deterministic
```

### Document ingestion makes the web service unresponsive

Move ingestion out of the web process:

```env
MY_AGENTS_INGESTION_EXECUTION_MODE=external_worker
```

Run the worker as a separate service/process using the same image and environment:

```bash
uv run python -m my_agents.ingestion_worker
```

### Suspected wrong database

Check startup diagnostics or add a short-lived safe DB summary log only. Never log the full
DB URL. Confirm only:

- scheme
- host
- database name

## Do not add host-specific branches

Avoid code like:

```python
if host == "render":
    ...
```

Prefer environment-selected behavior and provider boundaries. The current goal is portability:
Render, a VPS, or another PaaS should use the same application code with different runtime
configuration.
