---
created: 2026-05-18
updated: 2026-05-19
status: active
topics:
  - auth
  - email-verification
  - password-reset
related_code:
  - my_agents/auth/abuse.py
  - my_agents/auth/service.py
  - my_agents/auth/email.py
  - my_agents/api/auth.py
  - my_agents/settings.py
  - tests/test_auth_api.py
---

# Auth lifecycle: email verification and password reset tokens

## What changed

The auth flow moved from "signup can immediately login" to a more product-shaped lifecycle:

1. signup creates a user and a one-time email verification token;
2. the local development email sender records that token without requiring a paid email provider;
3. login is blocked until the email is verified;
4. password reset uses the same one-time-token pattern;
5. password reset revokes existing sessions after the password changes.
6. local auth abuse protection limits repeated signup, login, verification-token,
   and password-reset attempts before a real email provider or public guest mode is added.

## Important design idea

The backend stores only token hashes, not raw verification or reset tokens. This is the same safety idea as session-token storage: the raw token is bearer material, so the database should not need to keep it.

```mermaid
sequenceDiagram
    participant C as Client
    participant A as Auth API
    participant S as AuthService
    participant E as Local email sender
    participant DB as Database

    C->>A: POST /auth/signup
    A->>S: signup
    S->>DB: store user + token_hash
    S->>E: record verification email with raw token
    A-->>C: safe user + delivery status

    C->>A: POST /auth/verify-email token
    A->>S: verify_email
    S->>DB: match token_hash, consume token
    S->>DB: set email_verified_at
    A-->>C: safe verified user

    C->>A: POST /auth/login
    A->>S: login
    S->>DB: require verified email
    S->>DB: store session token hash
    A-->>C: HttpOnly session cookie + csrf_token
```

## Why local email first

Real signup email may use Resend, AWS SES, Firebase Auth, or another provider later. For this learning/backend milestone, the useful boundary is not the provider. The useful boundary is that auth code asks an `AuthEmailSender` to send verification/reset messages, while tests can use a deterministic local sender.

That keeps tests offline and prevents early provider cost or account setup from blocking backend design.

## Why local rate limiting first

Rate limiting is also a boundary decision. The first useful implementation does not need
Redis, Cloudflare, API Gateway, or a paid email provider. It needs one place where auth
routes ask, "is this action still allowed for this identifier in the current window?"

```mermaid
flowchart LR
    Request["Auth request"] --> Guard["AuthAbuseProtector"]
    Guard -->|allowed| Service["AuthService"]
    Guard -->|too many attempts| Block["HTTP 429"]
    Service --> DB[("Users / sessions / tokens")]
    Service --> Email["Local email sender"]
```

The current guard is intentionally in-process and replaceable:

- it is good enough for offline tests and single-process portfolio demos;
- it stores digested bucket keys instead of raw emails;
- it protects token guessing by counting invalid verification/reset-token attempts by client;
- it should move to Redis, a gateway, or another shared store before a real multi-worker public deployment.

## What to remember

- Email verification is account lifecycle state, not just an email-sending feature.
- Password reset should avoid account enumeration: the request endpoint returns the same accepted response even if no user exists.
- Reset tokens should be one-time use and expire.
- Changing a password should revoke old sessions.
- Rate limiting should protect both provider-cost endpoints and token-guessing endpoints.
- Guest mode should be treated separately because anonymous quota/rate-limit logic is a different product/security problem.

## Small exercise

Explain why the database stores `token_hash` for auth lifecycle tokens but the local development email sender still sees the raw token.

## Revision history

- 2026-05-18: Created learning log for `Auth lifecycle: email verification and password reset tokens`.
- 2026-05-19: Added local auth abuse-protection boundary and provider-readiness notes.
