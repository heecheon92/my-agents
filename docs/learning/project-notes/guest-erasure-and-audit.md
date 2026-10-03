---
created: 2026-10-03
updated: 2026-10-03
status: implemented
topics: [retention, transactions, audit, hmac, langgraph]
related_code:
  - my_agents/auth/guest_cleanup.py
  - my_agents/auth/models.py
  - my_agents/memory/store_projection.py
  - tests/test_guest_cleanup.py
---

# Removing a guest without forgetting its trial history

Expiration and deletion solve different problems. Expiration blocks access immediately;
deletion removes stored private content after the grace period. A guest that has exhausted
its prompt allowance can still read answers until expiry, so quota exhaustion alone is not
a deletion trigger.

The cleanup worker first locks and rechecks one expired account. Plain owner IDs do not
cascade through SQLAlchemy automatically: documents, derived chunks, conversations, sessions,
and other dependent rows must be erased in a deliberate order. Foreign-key-enabled tests
exercise this order. Shared ownership and active work cause deferral, not forced deletion.

Checkpoint and memory-projection erasure use framework APIs. Those stores and Product DB
cannot share a single application transaction here. Erase framework data while references
still exist, then atomically commit Product DB deletion plus its audit. A failed external
call leaves the references for retry; a later DB failure may leave an expired account whose
checkpoint has already gone. Repeating that deletion is safe. This does not promise an
all-or-nothing transaction across every store.

A content-free audit survives without a user FK. HMAC of the normalized requester email
supports comparing trial history without retaining that raw email in the audit. A plain hash
would allow guesses against common email addresses without a secret. HMAC still produces
pseudonymous data, and key loss/rotation affects future comparisons. The guest trial ledger now uses this history to enforce one trial per email, including after
account deletion. See the [policy](../../product-chat-service/40-guest-trial-abuse-protection.md).

## Revision history

- 2026-10-03: Explained the local cleanup implementation, retry boundary, and audit tradeoff.

- 2026-10-03: Linked the enforced trial ledger that now preserves eligibility through erasure.
