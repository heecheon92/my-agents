# Evidence bundle - production - 2026-06-06 13:23 UTC

Backend commit: 50b98f5 (`develop`)
Frontend commit: a7e6cf0 (`main`, observed production frontend/BFF)
Backend origin: production backend reached through `https://my-agents-frontend.vercel.app/api/my-agents/*`
Frontend origin: `https://my-agents-frontend.vercel.app`
Database/migration state: production Neon/pgvector accepted guest access, knowledge-base, document, ingestion, conversation, run, citation, and event writes; Alembic revision was not separately queried in this smoke.
Email/account provider mode: Resend HTTP via verified `my-agents.dev` sender; `/auth/dev/outbox` was not used.
OpenAI/runtime mode: production runtime; streamed run completed with answer deltas.

## Commands

- Backend tests: not rerun during this production smoke; latest code-level checks were run before commit `1a5260c`.
- Backend lint/format: not rerun during this production smoke; latest code-level checks were run before commit `1a5260c`.
- Frontend lint/typecheck/tests/build: not run from this backend smoke.
- Browser/e2e smoke: API-level production smoke through the Vercel BFF with `curl`, using same-origin headers and cookies.

## Smoke account

- Alias or redacted ID: plus-addressed owner Gmail alias, redacted from this evidence file.
- Verification path: guest request accepted, operator-issued code captured locally, `--send-email --lang ko` returned `email_sent=True`, and guest login succeeded through the production BFF.
- Confirmation that `/auth/dev/outbox` was not used for preview/production: confirmed; the guest code was issued from the operator script against the production pgvector env and delivered through the configured auth email provider.

## Product flow results

- Health: `GET /api/my-agents/health` returned `200` with `{"status":"ok","service":"my-agents","version":"0.1.0"}`.
- Frontend page: `GET /` returned `200` from Vercel.
- Guest request: `POST /api/my-agents/auth/guest/request` returned `200` with `{"status":"accepted"}`.
- Guest code issue: `scripts.issue_guest_access_code --env pgvector.production --send-email --lang ko` exited `0`, linked to an existing guest request, captured a code locally, and reported `email_sent=True` plus `email_language=ko`.
- Guest login/session restore: `POST /api/my-agents/auth/guest/login` returned `200`, set `my_agents_session` and `my_agents_csrf` cookies through the BFF, and `GET /api/my-agents/auth/me` returned `200` with `is_guest=true`, `email=null`, and a 24-hour `guest_expires_at` value.
- Guest conversation limit: first `POST /api/my-agents/conversations` returned `201`; second conversation attempt returned `429` with `guest conversation limit reached`.
- Knowledge base: `POST /api/my-agents/knowledge-bases` returned `201` for a personal KB.
- Document create: `POST /api/my-agents/knowledge-bases/{id}/documents` returned `201` for a small text document.
- Ingest/extraction evidence: `POST /api/my-agents/knowledge-bases/{kb_id}/documents/{document_id}/ingest` returned `200`.
- Streamed run: `POST /api/my-agents/conversations/{conversation_id}/runs/stream` returned `200`, emitted 24 `answer_delta` events, emitted `run_completed`, included one citation, and the answer contained the intentionally safe smoke fact from the document.
- Persisted run detail/citations: `GET /api/my-agents/conversations/{conversation_id}/runs/{run_id}` returned `200` with persisted citations.
- Run events: `GET /api/my-agents/conversations/{conversation_id}/runs/{run_id}/events` returned `200` with 5 events.

## Redaction and safety notes

- Email address, code, session cookies, CSRF token, document IDs, user IDs, KB IDs, conversation IDs, run IDs, and provider/database credentials are intentionally omitted.
- The guest code was captured only in a local temp directory for the duration of the smoke and was not pasted into this evidence file.
- This smoke intentionally created a short-lived guest identity, one conversation, one personal KB, one text document, one ingestion run, and one chat run in production.

## Remaining follow-up

- Add an explicit backend endpoint or deployment metadata field if future evidence needs to prove the exact deployed backend commit without relying on provider dashboards.

## Manual follow-up confirmation

The owner manually confirmed these production checks after the API-level smoke:

- Actual inbox receipt of the Resend guest-code email passed.
- Non-guest signup -> email verification -> login smoke passed.

## Post-smoke cleanup

After the smoke, production test-account cleanup removed the clear test set only:

- 8 users matching guest/test/smoke/demo/disposable-account criteria.
- 2 guest access requests and 2 guest access codes.
- Associated production smoke artifacts: 4 knowledge bases, 4 documents, 3 extraction runs,
  1 chunk, 1 conversation, 1 run, 1 citation, 5 run events, 6 auth tokens, and 5 sessions.

Verification after cleanup:

- Total remaining users: 1.
- Remaining guest users: 0.
- Remaining test-pattern users: 0.
- Remaining test-pattern guest requests: 0.
- Remaining guest access codes: 0.
- Production health still returned `{"status":"ok","service":"my-agents","version":"0.1.0"}`.

The local temp directory that held the captured guest code was deleted after cleanup.
