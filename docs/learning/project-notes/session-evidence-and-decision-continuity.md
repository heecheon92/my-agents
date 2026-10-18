---
created: 2026-10-18
updated: 2026-10-18
status: active
topics:
  - conversation-context
  - source-provenance
  - decision-continuity
  - transaction-safety
related_code:
  - my_agents/conversations/session_evidence.py
  - my_agents/conversations/continuity.py
  - my_agents/agents/general_assistant/responders.py
  - my_agents/api/conversations/graph_invocation.py
  - tests/test_session_evidence.py
---

# Remembering why an earlier answer was given

## Symptom

A user asked whether four document principles were actually available to the assistant during an
earlier turn. The assistant could see the conversation and a fresh document retrieval, but could
not establish which excerpts the earlier answer had received. It first accepted the correction as
an omission, then admitted that it could not verify the earlier input.

## What the code established

Visible messages were already retained. A metadata-only delivery manifest recorded source counts
and document references, but not the exact supplied excerpts. Historical-grounding metadata was
also only inserted when a summary, an older-history gap, or file notes existed. A short ordinary
conversation could therefore lack even that metadata. This does not prove why the earlier model
answered incorrectly; it establishes an application observability and continuity gap.

## Three different questions

| Question | Evidence needed |
| --- | --- |
| What did the user and assistant say? | Original transcript, with speaker and message links |
| What did the earlier model receive? | Record captured after final prompt packing, tied to that run |
| Why did the model choose that answer? | Explicit stated reasons or labeled inference; input availability alone cannot prove internal causation |

## Implemented approach

1. At the final input boundary, capture only bounded, authorized document excerpts and compact
   delivery facts. Do not copy full prompts, private reasoning, memory bodies, or tool internals.
2. After a successful provider call, store the record privately on its owning run. Only a
   completed run with a persisted assistant message can supply later recall.
3. Before a later answer, select a bounded set of earlier turns from the same conversation and
   recheck source access. Keep user statements, assistant claims, and recorded inputs distinct.
   Carry source dependencies into follow-ups even without new retrieval; otherwise a derived
   answer could retain revoked source content after the original answer is masked.
4. Inject those records through runtime context immediately at the response boundary. Do not
   return the recalled text from a graph node or feed it into Jev or summary generation.
5. Keep missing, partial, malformed, and unavailable evidence explicit. A later correction never
   changes an earlier snapshot. Regeneration must not see its replaced answer or future branch.

Adding a list to graph checkpoint state would be easy but would give source text another retention
and authorization path. Re-running retrieval would answer what is available now, not what was
available then. Asking another model to invent a retrospective rationale would not supply reliable
provenance. The implementation instead reuses the Product DB run lifetime and existing public APIs.

## Transaction issue found by the regression tests

The existing knowledge-base schema compatibility probe inspected a SQLAlchemy Engine. With the
single-connection SQLite test pool, that inspection could return the active connection to the pool
and roll back pending writes. The new evidence authorization path exposed this as an unexpected
active-run uniqueness failure. Inspecting `db.connection()` keeps the probe in the request-owned
transaction. A regression test flushes a run-policy change, records evidence, commits, and verifies
both survive. SQL parameter rendering is disabled so database errors do not print private records.

## Verification and limits

`tests/test_session_evidence.py` covers changed documents, later user corrections, unknown legacy
records, malformed records, permission removal, transcript masking, failed/cancelled runs, replay
boundaries, token limits, deletion, and provider-boundary integration. Existing workspace,
continuity, migration, and responder tests remain relevant. Final local verification on 2026-10-18:
1,197 passed, 42 skipped, 12 existing dependency warnings; Ruff lint and format checks passed.
The new feature contributes 25 offline scenarios, including the real graph's sync, SSE, and
optional durable-worker execution paths with a fake model boundary.

The initial policy recalls at most four of the latest 64 eligible turns. Recorded evidence survives
outside that window in Product DB, but there is no new tool to search arbitrary older records.
The model can still misinterpret available evidence. Offline tests prove delivery and isolation;
representative real-provider conversations are needed to evaluate explanation quality.

## Revision history

- 2026-10-18: Documented the transcript-versus-evidence gap, implemented boundary record, transaction
  regression, and bounded-recall/provider-fidelity limitations.
