# Deployment troubleshooting log

## Purpose

This file records concrete deployment problems, symptoms, root causes, and fixes. It is
for future operators and future agents who need to understand what happened without
re-reading chat history.

Use this file for repeatable operational lessons. Keep sensitive values out of it.

## 2026-05 Render public-demo signup stabilization

### 1. Docker build failed with BuildKit frontend error

**Symptom**

Render build failed before the backend container started:

```text
frontend grpc server closed unexpectedly
```

**What it was not**

- Not a Python/FastAPI application error.
- Not a backend port-binding issue.

**Likely cause**

Transient Render/Docker BuildKit builder instability while resolving the Dockerfile
frontend.

**Resolution**

- Retried the same commit later; build succeeded.
- Keep this as infrastructure noise unless it becomes frequent.

**Preventive option**

If this recurs, simplify/pin Docker build inputs:

- Remove `# syntax=docker/dockerfile:1` if no advanced Dockerfile features need it.
- Pin mutable images such as `ghcr.io/astral-sh/uv:latest` to a known version.

### 2. Render reported “No open ports detected” during deploy

**Symptom**

Render logs included:

```text
No open ports detected, continuing to scan...
```

**What it was not**

Not the root cause of signup failure. The app later logged:

```text
Uvicorn running on http://0.0.0.0:10000
```

and `/health`, `/auth/guest/request`, and `/auth/signup` reached the backend.

**Resolution**

No code change required. The existing container command was correct:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

### 3. Vercel signup returned 502 / 422 while backend logs were sparse

**Symptom**

Frontend requests to the Vercel API proxy surfaced backend failures as `502` or `422`.
Render initially showed only Uvicorn access logs.

**Root cause**

Insufficient backend diagnostics for hosted request, database, auth, and email steps.

**Resolution**

Added temporary `TEMP_DEPLOY_DIAG` logs for:

- safe runtime config summary
- request start/end/failure
- database session open/close
- signup lifecycle steps
- password hashing timing
- SMTP/Resend send start/end/failure

**Follow-up**

These logs are intentionally temporary. Remove them after public-demo signup and email
verification are stable.

See also:

- [`14-render-migration-and-rollback-notes.md`](./14-render-migration-and-rollback-notes.md)

### 4. Render appeared to use the wrong database

**Symptom**

Guest access request returned success, but expected rows were not visible in Neon.
This created suspicion that Render was using SQLite or another database.

**Root cause**

Not confirmed as a code defect. Redacted runtime diagnostics later showed Render was using
Neon/Postgres:

```text
db_scheme=postgresql+psycopg
db_host=...neon.tech
db_name=neondb
```

**Resolution**

Kept database URL diagnostics redacted. Never log full DB URLs because they include
credentials.

### 5. Signup created no user row before Argon2 tuning

**Symptom**

Signup reached:

```text
auth.service.signup.email_available
auth.service.signup.password_hash.start
```

but did not reach:

```text
auth.service.signup.password_hash.completed
```

No user row was committed.

**Root cause**

`argon2-cffi` default password hashing parameters were too heavy for the constrained
Render free runtime. Defaults were approximately:

```text
time_cost=3
memory_cost=65536 KiB
parallelism=4
```

**Resolution**

Made Argon2 parameters explicit and configurable, with deployable small-container defaults:

```env
MY_AGENTS_AUTH_PASSWORD_HASH_TIME_COST=2
MY_AGENTS_AUTH_PASSWORD_HASH_MEMORY_COST_KIB=19456
MY_AGENTS_AUTH_PASSWORD_HASH_PARALLELISM=1
```

Hosted diagnostics then showed password hashing completing in roughly 200-300 ms.

**Keep or rollback?**

Keep. This is portable runtime tuning, not a Render-only branch.

### 6. Signup committed user but failed through Resend SMTP timeout

**Symptom**

Signup reached:

```text
auth.service.signup.db_committed
auth.email.smtp.start host=smtp.resend.com port=587
```

then failed with:

```text
TimeoutError
```

The API returned `500` after the user row had already committed.

**Root cause**

Render free web services block outbound SMTP ports such as `25`, `465`, and `587`.

**Resolution**

Added `resend_http` auth email mode using Resend HTTPS API over port `443`:

```env
MY_AGENTS_AUTH_EMAIL_MODE=resend_http
RESEND_API_KEY=<redacted>
MY_AGENTS_AUTH_FROM_EMAIL=<verified-sender>
MY_AGENTS_RESEND_API_URL=https://api.resend.com/emails
```

Generic `smtp` mode remains available for hosts that allow SMTP.

### 7. Resend HTTP returned 403 Forbidden with `onboarding@resend.dev`

**Symptom**

Resend HTTP request reached the API but failed:

```text
HTTPStatusError: Client error '403 Forbidden'
```

Runtime diagnostics showed:

```text
from_domain=resend.dev
```

**Root cause**

`onboarding@resend.dev` is a Resend testing/sandbox sender and is restricted. It cannot be
used for arbitrary public signup recipients.

**Resolution**

- Bought `my-agents.dev`.
- Added Resend DNS records in Cloudflare.
- Waited for Resend domain verification.
- Changed sender to:

```env
MY_AGENTS_AUTH_FROM_EMAIL=no-reply@my-agents.dev
```

Hosted signup then completed with:

```text
auth.email.resend_http.completed
auth.api.signup.completed
POST /auth/signup 201 Created
```

### 8. Cross-encoder reranker may be heavy for small hosts

**Symptom**

No confirmed failure yet in this incident, but runtime config shows:

```env
MY_AGENTS_RERANKER_MODE=cross_encoder
```

Cross-encoder reranking loads ML dependencies and can be memory-heavy.

**Rollback**

If Render free memory/startup becomes unstable, use:

```env
MY_AGENTS_RERANKER_MODE=deterministic
```

## Cleanup checklist after stable demo

Once hosted signup, email verification, login, guest access, and a basic chat run are stable:

1. Remove temporary `TEMP_DEPLOY_DIAG` diagnostics.
2. Keep Argon2 config and Resend HTTP sender.
3. Update older docs that still describe SMTP as the only hosted email path.
4. Run targeted auth/settings/email tests.
5. Redeploy and perform one hosted smoke test.

## Entry template

```markdown
### N. Short problem title

**Symptom**

What the operator saw.

**Root cause**

What actually caused it.

**Resolution**

What changed.

**Rollback / follow-up**

What to do if it returns, or what cleanup remains.
```
