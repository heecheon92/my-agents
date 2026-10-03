# Guest trial eligibility and abuse protection

English | [한국어](./40-guest-trial-abuse-protection.ko.md)

A normalized email receives **one guest trial**, unless an operator explicitly resets it.
Requesting or redeeming another code while that trial is active signs into the same guest account;
it does not renew expiry, replenish quotas, or send another account-creation notification.
An expired or deleted trial cannot be recreated through public guest endpoints. Any existing
registered account, including pending/rejected signup, makes its email ineligible for guest access.

## Identity, codes, and transactions

`guest_trials` has one primary-key row per HMAC of the normalized email (`strip().casefold()`).
This is the same fingerprint as the deletion audit and uses the existing
`MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY`. The name remains compatible, but the key is now required
when **guest access or cleanup** is enabled. Use a stable random secret of at least 32 bytes.
No raw email is stored in the trial ledger or rate-limit buckets. Email request rows remain the
source for delivery and code ownership. Provider-specific aliases (plus addressing, Gmail dots,
or multiple mailboxes) are not collapsed by this policy.

A unique identity insert plus a database write/row lock serializes issuance, redemption, and
operator reset for that email. Issuance invalidates earlier unused codes and binds the new code
to the current trial generation. Redemption rechecks eligibility and conditionally consumes the
code inside the transaction that creates/reuses the account, creates its session, and records any
new-account notification. A transaction failure restores code eligibility. Duplicate redemption
cannot create a second account; even two distinct valid codes for one identity reuse one account.
Email-less operator codes cannot create trials under this policy.

Guest-session authentication also checks the ledger's selected account. This blocks legacy
duplicates or accounts created by an old application instance after reconciliation. Regular
signup and invitation signup serialize against the same identity while guest access is enabled.
A registered account must use its ordinary signup/verification/approval path, not guest access.

## Request limits and public responses

Defaults, configurable in production:

```dotenv
MY_AGENTS_GUEST_CODE_RESEND_COOLDOWN_SECONDS=60
MY_AGENTS_GUEST_CODE_EMAIL_DAILY_LIMIT=5
MY_AGENTS_GUEST_REQUEST_IP_HOURLY_LIMIT=30
MY_AGENTS_GUEST_LOGIN_IP_WINDOW_LIMIT=60
```

The login-origin window is 15 minutes. Email delivery/code issuance has a 60-second cooldown and
five-code UTC fixed-day budget. Origin budgets use shared Product DB fixed-window counters, including
failed login attempts; changing workers or restarting the app does not reset them. Expired buckets
are pruned opportunistically on subsequent budget checks. Boundaries can permit a burst spanning
two windows; this is not a sliding-window limiter.

The origin is `request.client.host` after the server's trusted proxy handling. The application does
not trust arbitrary `X-Forwarded-For` headers. Behind a BFF/proxy, multiple visitors may share that
origin and its budget unless deployment-level trusted forwarding preserves client addresses.
Verify this before interpreting these counters as per-browser or per-person limits.

`POST /auth/guest/request` returns the same `{"status":"accepted"}` for eligible emails, used trials,
registered emails, and suppressed email resends. Acceptance is not proof an email was sent.
Origin-budget exhaustion returns HTTP 429 (`rate_limit_exceeded`); invalid/expired/reused codes
retain the existing HTTP 400 contract. Provider failure keeps the existing 503 behavior. In local
mode, email is captured offline. Existing approved operator issuance remains email-bound and subject
to eligibility/cooldown/email budgets, but has no network-origin budget.

`GET /auth/guest/policy` additionally advertises `trial_policy=one_per_email`,
`active_trial_relogin_supported=true`, `code_resend_cooldown_seconds`, and `code_email_daily_limit`.
Frontend guest copy should explain the one-trial policy and that an accepted request may not send
a code. This backend change does not edit or deploy the separate frontend repository.

## Rollout and historical data

1. Keep the existing HMAC key. Set it before enabling guest access, even if cleanup is disabled.
2. Temporarily disable guest issuance/login on the old deployment while migrating. Avoid mixed
   old/new auth writers during rollout; schema changes alone cannot repair the old implementation.
3. Apply migration `20261003_0039` with `uv run alembic upgrade head`.
4. Preview reconciliation using `uv run python -m scripts.guest_trial_policy backfill`.
   The command rolls back its changes unless `--apply` is passed. It reports identity counts only.
5. Apply with `uv run python -m scripts.guest_trial_policy backfill --apply`, deploy the new app,
   and re-enable guest access. New app startup also performs this idempotent bootstrap before serving
   requests when guest access is enabled; service calls provide the same barrier as a fallback.

Bootstrap incorporates redeemed requests/codes, existing guest users linked to them, and deletion
audits. For each email it keeps the oldest still-active guest (creation time, then ID), without
changing its original expiry or quota. Other active guests for that email are expired immediately
and their sessions revoked. Their private data stays for the configured cleanup grace period.
Registered emails retain no active guest trial. Duplicate unused legacy codes are invalidated;
only the latest eligible one remains usable. Accounts with no verifiable email/trial link cannot
continue through guest-session authentication, but their data is not deleted by reconciliation.

`guest_policy_state` stores a key verifier and initialization marker atomically with backfill.
Changing the HMAC key later fails closed instead of silently granting another set of trials.
The initial key must match the one used for historical deletion audits: their raw emails cannot
be recovered to repair a wrong key. Automated key rotation is not implemented. For application
rollback, keep guest access disabled rather than dropping the ledger or allowing old auth writers.

## Operator-only reset

Preview and explicitly grant another trial:

```bash
uv run python -m scripts.guest_trial_policy reset --email guest@example.com
uv run python -m scripts.guest_trial_policy reset --email guest@example.com --apply
```

The command uses the configured database; production targeting is an operator decision.
There is no public reset API. Reset increments the generation, records its time, expires the
previous guest and revokes sessions, and invalidates old codes. The next eligible code redemption
creates a fresh trial. It neither deletes old private data nor bypasses the registered-email rule
or email-send budgets. Commands do not print the supplied email or any code/credential.

## Cleanup and verification

Cleanup retains the trial record and its redeemed status while deleting guest content. It clears
the pointer to the deleted account, removes pending codes belonging to that same trial generation,
and retains the existing minimal deletion audit. Cleanup and reset take the identity lock before
the user lock so concurrent operations cannot erase a newly granted trial.

Offline tests cover sequential and concurrent issuance/redemption, shared budgets across sessions,
re-login without renewal, registered emails, reset/code invalidation, rollback, historical duplicate
reconciliation, key mismatch, and registration attempts after account cleanup. File-backed SQLite
uses real foreign keys and independent sessions for concurrency tests. PostgreSQL deployment
concurrency and trusted-proxy address behavior require separate verification. Local in-memory
SQLite is only for sequential smoke tests; production guest access requires persistent storage.
The rule prevents same-email quota renewal, not the use of multiple mailboxes or unrelated
registered-account abuse.
