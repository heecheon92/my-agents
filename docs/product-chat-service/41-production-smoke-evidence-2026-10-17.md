# Hosted smoke evidence — 2026-10-17

Status: Passed for the requested authenticated document/chat smoke; completed 2026-10-17 at approximately 11:53 UTC.

Canonical status: [implementation tracking](../implementation-tracking.md#current-overall-status).

## Scope and environment

The owner requested backlog reconciliation and one hosted smoke: login/session -> small document
upload -> completed ingestion -> cited answer -> refresh with persisted answer/citations.
Frontend repository changes, release publication, and deployment changes were outside the smoke
scope. The owner subsequently authorized committing and pushing these documentation records.

- Frontend: `https://www.my-agents.dev`.
- Backend access: same-origin `/api/my-agents/*` BFF.
- Remote backend heads at smoke time, before the documentation commit: `main` = `3b6fd23`, `develop` = `1f654c5`, verified by refreshed refs and
  `git ls-remote`; divergence `0 / 1`. The extra commit adds interactive guest reset.
- Exact deployed backend/frontend SHAs and production migration revision: not inspected.
- Production provider configuration and worker topology: not inspected.

## Observed results

Public HTTP checks completed at approximately 11:44 UTC on 2026-10-17:

| Check | Result | What it proves |
| --- | --- | --- |
| Frontend landing/login pages | Rendered in dedicated Chrome tab | Public navigation reaches the login form |
| `GET /api/my-agents/health` | HTTP 200, `status=ok`, service `my-agents`, version `0.1.0` | BFF reaches the backend health route; upload concurrency hint is 1 |
| `GET /api/my-agents/auth/guest/policy` | HTTP 200, enabled, automatic email, `trial_policy=one_per_email`, active re-login supported | Current public policy contract is served |
| `GET /api/my-agents/auth/me` without cookies | HTTP 401 | Anonymous request is rejected |
| Authenticated session | Passed after owner sign-in | Chat and personal knowledge-base views render in the authenticated browser session |
| Small document upload and ingestion | Passed: 425-byte Markdown upload, embedding/metadata progress, then ready | Uploaded fixture is parsed and available for retrieval |
| Cited answer | Passed: all four fixture facts correct, uploaded Markdown marked as supporting evidence | The answer is grounded in the new document |
| Refresh persistence | Passed: session restored, same answer and source panel available after full page reload | Completed answer and citation presentation survive browser refresh |

Public guest policy also reported a 900-second code TTL, 86,400-second session TTL,
3 conversations, 20 prompts, 5 document uploads, 60-second resend cooldown, and
5 code emails per day. These values are observations, not new defaults or proof of enforcement.

## Authenticated workflow

The owner signed in through the browser. A single synthetic `hosted-smoke-2026-10-17.md`
file was uploaded to an existing personal test knowledge base through the normal upload dialog.
Progress moved through embedding generation and metadata processing, then showed one of one
files available. The document preview rendered the fixture text correctly.

A new private conversation selected that personal knowledge base and asked for the fictional
Lantern Orchard drill's verification phrase, crate count, review window, and retention period.
The prompt named the document but did not contain the expected answer values. The response gave:

- Verification phrase: `COPPER-KITE-731`.
- Sample crate count: `17`.
- Review window: `09:20–09:35 UTC`.
- Sample retention period: `11 days`.

The answer explicitly named the Markdown source. The source panel contained two references,
with the uploaded Markdown marked as supporting evidence (`근거`). An unrelated existing PDF
was also listed without that marker. This is a retrieval-quality observation for the next
fixture/evaluation work, not an established frontend defect; no unrelated PDF contents were opened.

A full browser reload restored the authenticated session, identical answer facts, source label,
and expandable two-source panel with the same supporting-evidence marker. The composer displayed
`gpt-5.6-sol` with medium effort; backend model pin/provider configuration was not independently
queried. Browser progress and final rendering were observed, not raw SSE frame counts.

## Remaining gates

- Broader signup/email verification, group invitation/publish workflow, long-idle recovery,
  disconnect persistence, guest-policy enforcement, model/provider matrix, and worker recovery
  remain separate checks. This small smoke cannot close those items.
- No blocking frontend defect was observed in this workflow. Any required frontend work
  must be reported to the owner; the sibling repository is outside this task.

## Local checks

The unchanged runtime passed `uv run pytest -q`: 890 passed, 14 skipped, 12 deprecation warnings
in 131.34 seconds. Ruff lint and formatting passed (294 files). Documentation file links,
heading anchors, diff whitespace, and credential-pattern checks passed. These local results do
not replace broader PostgreSQL checks or the live model/provider matrix.

## Data handling

No credentials, account identifiers, cookies, tokens, or private document contents belong in
this record. Sign-in is performed by the owner in the browser; `.env` contents are not read.
A 425-byte synthetic Markdown fixture was prepared locally with a fictional drill name,
verification phrase, crate count, review window, and retention period. It contains no personal
information or real operational policy. One smoke document, its ingestion output, and one private
conversation/answer were created and retained for owner inspection. Existing documents and
conversations were not edited or deleted. Identifiers and account details are omitted here.

The first automated file-selection attempt was rejected because the Chrome extension's
"Allow access to file URLs" permission was disabled. The owner enabled the permission;
retry succeeded without an application change. This was
a resolved browser-tooling blocker, not an established frontend defect. A local screenshot of
the refreshed result was captured with the account/history sidebar collapsed; it is not tracked
in the repository.
