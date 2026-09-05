# Temporary conversation document workspace

- Status: Shipped in released backend/frontend code; enablement and production workflow verification are separate.
- Recorded: 2026-09-05
- Integration evidence: backend `92d7a50`; frontend `af0176c`.
- Release evidence: backend release `7a450cc` / later hotfix `e62d45a`, frontend `9c8e365` on 2026-09-05.
- Current behavior: [workspace contract](../product-chat-service/en/25-openai-document-workspace.md).
- Canonical status: [implementation tracking](../implementation-tracking.md#shipped-and-completed-index).

## Delivered scope

Capability-gated attachments, per-upload provider consent, local staging, selected file IDs on
runs, refresh-safe metadata, and authenticated binary artifact downloads are integrated.
The backend recognizes Office, PDF, Markdown, and HTML outputs from its versioned allowlist.
Temporary conversation files remain distinct from durable knowledge-base ingestion.

## Decisions and boundaries

Guests remain ineligible. File bytes stay with the expiring provider workspace; Product DB keeps
metadata and usage. Download availability does not certify content accuracy or rendering fidelity.
The frontend derives output eligibility from served capability data.
Attachment-free artifact generation remains deliberately deferred.

## Acceptance evidence

Backend validation recorded for `92d7a50`: 572 passed, 3 skipped, Ruff lint/format passed.
Frontend implementation log at `af0176c` records 351 unit tests, successful type/lint/build checks,
185 browser passes, 2 environment-gated skips, and one independently reproduced pre-existing
conversation-list focus failure. These are historical results, not tests rerun for this record.
The owner reported successful live XLSX generation in the 2026-09-02 session.

## Known limitations and deferred follow-ups

- The containing frontend/backend releases were deployed; that does not establish workspace
  enablement or an authenticated production attachment/artifact workflow. Those remain unverified.
- Live generation quality for every newly allowed format remains unverified.
- Artifact query invalidation was fixed after live use revealed missing download controls.
  The frontend log explicitly records the missing regression test because the mock harness could
  not reliably complete the relevant existing-conversation stream.
- File generation still requires selected attachments.

## Historical implementation record

See the preserved [frontend rollout plan](../product-chat-service/en/29-frontend-document-workspace-rollout.md)
and the sibling frontend's `docs/implementation-log.md`, section
`2026-09-02 — temporary conversation files`, for implementation decisions and verification details.
