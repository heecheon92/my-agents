# Expired guest account cleanup

English | [한국어](./39-expired-guest-cleanup.ko.md)

Guest expiry revokes access immediately. Optional cleanup permanently removes private guest
resources after a grace period, defaulting to 24 hours after `guest_expires_at`. Running out of
prompts or uploads does not trigger deletion; users can still read their results until access
expires. Registered accounts, unexpired guests, and guests inside the grace period are excluded.

## Enable and preview

Apply migration `20261003_0038` with `uv run alembic upgrade head`, then configure:

```dotenv
MY_AGENTS_GUEST_CLEANUP_ENABLED=true
MY_AGENTS_GUEST_CLEANUP_GRACE_SECONDS=86400
MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY=<stable-random-secret-at-least-32-bytes>
```

Create the HMAC key in your own terminal, for example `python -c 'import secrets; print(secrets.token_hex(32))'`,
and store it in your deployment's secret environment settings. Never commit it. Preserve the same key
across restarts/instances; changing it breaks comparison with historical email fingerprints.
The key is separate from provider credentials. Cleanup is disabled by default and does not depend
on the registration-notification recipient or on guest registration still being enabled.
File-backed SQLite or PostgreSQL is required when cleanup is enabled; memory SQLite is rejected.

Preview the configured database without deleting accounts:

```bash
uv run python -m scripts.cleanup_expired_guests
```

The CLI reports aggregate counts by outcome, not emails, documents, or credentials. `--apply`
permanently erases eligible accounts and additionally requires cleanup to be enabled. A normal
FastAPI process starts the enabled worker automatically after restart; each sweep examines up to
25 guests, then waits 60 seconds. Keyset pagination advances past deferred accounts rather than
letting the oldest blocked guest prevent cleanup of others. The CLI walks all pages in one pass.

## Deleted data

- Guest user, sessions, auth tokens, redeemed guest codes, and unreferenced associated requests.
- Private personal KBs, documents, chunks/embeddings, parser artifacts, metadata, extraction runs,
  structured facts, and entity mentions/relationships. Only newly orphaned entity records are removed.
- Conversations, messages, summaries, runs, events, citations, memory settings/suggestions/records,
  user-scoped memory projections, and usage rows. Run checkpoints are deleted by `run_id`.
- Guest membership/permission links. Accepted-invitation user references are detached without
  deleting the group or other members. Guest-created publication request snapshots are removed;
  separately owned published copies remain intact.

Existing registration notification events are independent of the account and remain deliverable.
Their email addresses are cleared on delivery as described in the auth contract. Requests still
referenced by another code remain intact. Shared resources the guest merely accessed are preserved.

## Deferred cases and failure handling

The whole account is deferred if it has a running/cancelling run, pending/running ingestion,
shared/group/system resource ownership, private documents granted to another user, a directly
published KB, or document evidence used by another user's run. Inconsistent ownership or multiple
request emails also require review. Groups owned/created by a guest need ownership resolution first.
No automatic ownership transfer or cancellation of active work is implemented.

Guests cannot normally use provider workspaces. Unexpected legacy workspace/attachment rows defer
erasure for operator review; the worker does not pretend external provider files were removed.
Resolve the reported condition and the next scan retries. Stale active rows can therefore defer
cleanup indefinitely; the worker logs aggregate defer reasons, and the CLI exposes the same counts.

Each account is rechecked under a write lock. Checkpoints and memory projections are removed using
framework APIs before the Product DB transaction deletes content and records its audit. If an
external deletion fails, Product DB rows and references remain for retry. If Product DB commit fails
after external erasure, the expired account remains and external deletions can safely repeat.
This is retryable erasure, not a transaction spanning the framework stores and Product DB.
Worker shutdown finishes the current account and stops before claiming another; it waits before
closing the shared persistence resources. Failures log exception types, never raw database payloads.

## Retained evidence and email reuse

`guest_deletion_audits` retains the deleted guest ID, creation/expiry/deletion timestamps, a reason,
aggregate resource counts, and an HMAC-SHA256 fingerprint of the normalized requesting email.
It stores no raw email, document name/body, prompt, answer, or credential. Email-less operator codes
produce a null fingerprint. The audit has no user foreign key and survives account deletion.

The separate [guest trial ledger](./40-guest-trial-abuse-protection.md) now enforces one trial per
email and survives cleanup. This audit also seeds historical eligibility during initial rollout. HMAC history is
pseudonymous, not anonymous, and has no automatic expiry in this first implementation. Existing
application/infrastructure logs and backups are outside this database cleanup's scope.

## Verification boundary

Offline tests use file-backed SQLite with foreign keys enabled and cover owned-data erasure,
registered/other-user preservation, grace periods, dry run, concurrent cleanup, rollback/retry,
checkpoint failure, orphan/shared entities, memory projection cleanup, and deferred active/shared
resources. Migration/schema checks and the full offline suite are required before handoff.
Production PostgreSQL concurrency, hosted checkpoint deletion, and deployed scheduling need
separate verification. No production cleanup is executed as part of local implementation.
