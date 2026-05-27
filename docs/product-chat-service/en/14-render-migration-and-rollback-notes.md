# Render migration and rollback notes

## Purpose

This document preserves the deployment decisions made while stabilizing the backend on
Render. The goal is to keep the codebase portable: moving away from Render should mostly
mean changing environment variables and removing temporary diagnostics, not rewriting auth,
email, or database logic.

## Current Render-specific findings

- Render free web services have limited memory. Signup previously stopped during Argon2
  password hashing when the app used the `argon2-cffi` library defaults.
- Render free web services block outbound SMTP ports such as `25`, `465`, and `587`.
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

## Temporary diagnostics to remove after hosted signup is stable

The following were added to debug deployment behavior and should not become permanent product
surface area:

- `my_agents/diagnostics.py`
- `TEMP_DEPLOY_DIAG` request middleware logs
- signup step-by-step diagnostic logs
- password hash timing logs
- database session lifecycle diagnostic logs
- SMTP/Resend temporary deploy diagnostics beyond normal operational logs

Recommended cleanup approach:

1. Confirm hosted signup completes and verification email is delivered.
2. Create one cleanup commit that removes only the temporary diagnostics.
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
   - On small hosts, switch to `MY_AGENTS_RERANKER_MODE=deterministic` if startup or memory is unstable.

7. **Diagnostics cleanup**
   - Remove temporary diagnostics before treating the deployment as stable.
   - Keep only normal operational logs that do not expose secrets or raw emails.

## Rollback recipes

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
