# Frontend rollout for temporary conversation files

[한국어](../ko/29-frontend-document-workspace-rollout.md) | English

Status: **Proposed — immediate cross-repository task managed from the backend roadmap.** The
backend capability exists; the frontend and BFF do not expose it to users yet.

## Product opportunity

Users should be able to attach a temporary file directly to a conversation, ask the assistant to
analyze or transform it, and download certified generated spreadsheet artifacts. This is different
from uploading a durable document into a knowledge base:

| Workflow | Purpose | Lifetime | Retrieval behavior |
| --- | --- | --- | --- |
| Knowledge-base upload | Reusable source for later questions | Durable Product DB content and indexes | Ingested and retrieved through RAG |
| Conversation attachment | Analyze or transform a file for this conversation | Expiring OpenAI file/container; Product DB metadata only | Sent directly to the document workspace for selected runs |

The distinction must be visible before upload. A user choosing a temporary attachment should not
believe the file becomes searchable knowledge, and a user adding knowledge should not accidentally
consent to provider-hosted document execution.

## Backend readiness

The implemented backend already provides:

- `GET /capabilities/document-workspace` for enablement, eligibility, accepted formats, limits,
  output certification, and retention;
- conversation-scoped attachment create/list/delete routes;
- additive `attachment_ids` on conversation run requests;
- run responses and safe events containing attachment/artifact metadata;
- conversation-scoped artifact list and authenticated download routes;
- per-upload `provider_consent=true`, ownership checks, guest rejection, expiring OpenAI
  `user_data` files, and a network-disabled hosted container;
- GPT-5.6 Sol execution with Hosted Shell and the spreadsheet skill when applicable.

The frontend currently has no attachment models, BFF routes, service client, composer action,
selection state, or artifact presentation. Therefore the capability is API-complete but
product-inaccessible.

## User-visible features to introduce

### Attach and inspect

- Add one attachment action to the chat composer, available only when the served capability says
  `enabled=true` and `eligible=true`.
- Use the capability's format registry and numeric limits; do not hardcode extensions, file counts,
  byte limits, or retention.
- Show local validation before provider consent: filename, type, size, unsupported/empty/too-large
  errors, and the combined selected size.
- Ask for explicit OpenAI transfer consent before each upload. The action must name the transfer,
  temporary retention, and the difference from durable knowledge-base storage.
- Show upload, available, expired, deleting, deleted, and failed states with retry only where the
  backend contract makes retry safe.

### Select files for one run

- Send only explicitly selected available attachment IDs in `attachment_ids`.
- Keep selection visible beside the composer and let the user remove an attachment before sending.
- Recover conversation attachment metadata after refresh without reviving expired or deleted files.
- If upload succeeds but run creation fails, preserve the available attachment so the user can
  retry without transferring the same bytes again.

### Present results honestly

- Show safe `attachments_ready`, `document_workspace_started`, and `artifact_created` activity.
- List generated artifacts on the answer/run that created them and provide authenticated download
  actions while available.
- Clearly distinguish analysis support from certified editable output. The current stable output
  contract certifies only `.xlsx`, `.csv`, and `.tsv` files written under `/mnt/data/output/`.
- For PDF, DOCX, PPTX, text, code, and other accepted inputs, promise analysis only unless the
  capability explicitly upgrades their artifact status. Do not claim fidelity-preserving editing.

## Conversation creation decision

The attachment endpoint requires a conversation ID, while bare `/chat` currently creates no
conversation until the first message is sent. Preserve that no-empty-conversation invariant.

Recommended first implementation: stage selected files locally on bare `/chat`; when the user sends
the first prompt, create the conversation, upload each consented file, then start the run with the
successful attachment IDs. If all uploads fail, keep the prompt and local selections available and
do not start an attachment-free run silently. This decision must be browser-tested because route
replacement during the first run previously caused streaming state loss.

## Constraints and caveats

- Feature disabled or ineligible means the composer renders no misleading attachment control.
- Guest sessions remain ineligible.
- File bytes are not stored in Product DB. Metadata remains after provider expiry for honest status
  and usage audit.
- Expiry can occur while a conversation remains open; disable selection/download immediately when
  server state says expired and provide a re-upload path.
- Deleting an attachment must not optimistically hide a failure that leaves provider data active.
- Limits apply both per file and across the selected run. Backend validation remains authoritative.
- Upload and execution are billable provider operations. Duplicate submission and retry must not
  create duplicate usage records.
- Attachment instructions and file contents are untrusted input. Never surface Hosted Shell
  commands, stdout, provider traces, hidden reasoning, or raw errors.
- Mobile presentation must keep attachment removal, consent, and send controls reachable without a
  nested horizontal scroller.

## Implementation sequence

1. Serve and capture the feature branch OpenAPI; generate attachment, artifact, capability, and run
   request/response models from it.
2. Add exact BFF allowlist rules for capability, attachment CRUD, artifact list, and artifact
   download. Preserve streaming and authenticated download headers.
3. Add frontend service/query/mutation boundaries and cache keys.
4. Implement composer-local staging, consent, upload queue, available selection, and refresh
   recovery.
5. Add `attachment_ids` to normal streamed runs while preserving reasoning and knowledge-source
   controls.
6. Render safe activity and run-scoped artifacts with expiry-aware download actions.
7. Verify existing-conversation and bare-new-chat flows at 390, 768, and 1280 pixels.
8. Run one owner-approved credentialed E2E using a small non-sensitive file; keep automated tests
   offline with the provider mocked.

## Definition of done

- A registered eligible user can attach, remove, select, and reuse an available temporary file.
- The same user can ask for analysis and receive the answer without adding the file to a knowledge
  base.
- A certified spreadsheet transformation produces a downloadable artifact with honest expiry.
- Unsupported, oversized, expired, deleted, partially uploaded, and provider-failure paths preserve
  the prompt and never start a misleading run.
- Refresh and replay keep attachment/artifact metadata tied to the correct run.
- Guests and disabled deployments expose no usable attachment path.
- No provider internals, hidden reasoning, credentials, shell output, or raw file bytes enter public
  events or frontend caches.

Current backend execution details remain authoritative in the
[OpenAI-hosted document workspace contract](./25-openai-document-workspace.md).
