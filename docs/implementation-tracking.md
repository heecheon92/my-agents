# Implementation tracking

Last updated: 2026-05-20
Status owner: repo-tracked source of truth for cross-machine agent handoff

This file exists because `.omx/` is local runtime state and is not shared across machines. When working with an agent on any machine, start here before re-discovering project status from the codebase.

Use this file to answer: **"What should we do next?"** without first re-reading the whole codebase. For detailed backlog coverage, use [`../ROADMAP.md`](../ROADMAP.md) as the companion checklist.

## Source-of-truth contract

- `docs/implementation-tracking.md` is the canonical cross-machine handoff summary: current status, latest verification, known gaps, recommended next workflow, and completed milestone log.
- `ROADMAP.md` is the detailed roadmap/checklist backlog: broader v1 scope, deferred items, and definition of done.
- If both files mention the same item, this file decides current priority and freshness; update `ROADMAP.md` afterward so the checklist does not drift.
- If `.omx/` and this file disagree, treat this file as the portable baseline and `.omx/` as machine-local context only.

## How to use this file

- Start here when the user asks what to do next, what is incomplete, or what changed since another machine/session.
- Update this file when a workflow meaningfully changes project completion, next priorities, or known gaps.
- Keep it factual: record implemented behavior, verification evidence, and remaining risk separately.
- Prefer short entries with links to code/docs/tests instead of long narrative.
- Do not use this file for secrets, local `.env` values, or machine-specific runtime state.

## Current overall status

| Scope | Status | Completion estimate | Notes |
| --- | --- | ---: | --- |
| Portfolio-quality backend v0 | In progress, strong foundation | 90-93% | Thin end-to-end backend slice exists; auth lifecycle includes Phase 1 single-process public-demo hardening, conversation runs stream over SSE, completed run detail is refresh-safe, local V1 demos have seed/smoke helpers plus frontend credentialed CORS/demo runbook, and strict V1 contract/evidence mapping is explicit. |
| Production SaaS readiness | Early | 49-54% | Account lifecycle improved and generic SMTP email delivery is code-ready; still needs provider secret setup, hosted deployment verification, production ingestion, and ops work. |
| Full AI agents product vision | Early/mid | 25-35% | Current production graph is one assistant/router path; richer agent/tool workflows are future milestones. |
| Learning/practice simulated agents | Active learning lab | Ongoing | `my_agents/simulated_agents/` is meaningful practice code, intentionally separate from production API/CLI surfaces. |

## Implemented and verified baseline

### Backend/API foundation

- FastAPI app factory and route assembly: `my_agents/api/__init__.py`
- Health endpoint: `GET /health`
- Legacy/dev assistant smoke endpoint: `POST /assistant/chat`
- Product conversation-run surface under `/conversations`
- Auth, groups, documents, knowledge bases, and run events are registered routes.

### Assistant graph

- Production-surface assistant graph lives in `my_agents/agents/general_assistant/`.
- Uses LangGraph `StateGraph` with explicit classification and response nodes.
- Classification is deterministic.
- OpenAI-backed response generation uses `langchain-openai` / `ChatOpenAI` by default.
- Deterministic mode remains available for tests and offline smoke checks.
- Hosted web search can be attached at the response-provider boundary for research/general current-info requests in OpenAI mode.

### Auth/session foundation

- Email/password signup.
- Local/offline auth email sender boundary for verification and reset messages.
- Email verification token creation and `POST /auth/verify-email`.
- Verified-email login with app-owned opaque session cookie.
- CSRF token support for logout.
- `/auth/me` for current user lookup.
- Password reset request/confirm flow with non-enumerating request responses.
- Password reset revokes existing sessions.
- Password hashes and raw token hashes are not returned by API responses.
- Local in-process auth abuse protection covers repeated signup, bad login, reset request, and invalid lifecycle-token attempts.

### Groups, documents, permissions

- Group creation/list/get.
- Member add/update flows.
- Document create/list/get.
- Document permission patching.
- Authorization service for read/write/manage/ingest decisions.

### Conversations and runs

- Server-owned conversations.
- Persisted user and assistant messages.
- Conversation run endpoint invokes the current graph.
- SSE conversation-run stream emits redacted progress events, `answer_delta` assistant text chunks, and a final run response.
- Run summaries and run activity events are persisted and readable.
- Failure path records a failed run with redacted event metadata.

### Knowledge/RAG prototype

- Knowledge-base create/list.
- Text document ingestion remains compatible.
- PDF-first upload path with safe metadata persistence and page provenance.
- Deterministic chunks, embedding fixtures, entity mentions, and co-occurrence relationships.
- Permission-aware retrieval filters candidate chunks before ranking/expansion/composition.
- Citation-backed replies for retrieved authorized context.

### Persistence and migrations

- SQLAlchemy models cover auth, auth lifecycle tokens, sessions, groups, documents, knowledge artifacts, conversations, runs, events, and citations.
- Alembic migrations cover the initial service schema, auth lifecycle, run detail refresh fields, and PDF upload provenance fields.
- SQLite in-memory auto-create supports offline tests.
- Postgres/Neon readiness is documented, with external DB tests skipped unless configured.

### Documentation and learning support

- Bilingual root README pair: `README.md`, `README.en.md`.
- General assistant README pair under `my_agents/agents/general_assistant/`.
- Portfolio architecture notes under `docs/portfolio-chat-service/`.
- Personal learning logs and agent-lab notes under `docs/learning/`.
- Simulated-agent candidate materials exist for future learning/practice ideas.

## Latest verification evidence

Last full local verification run: 2026-05-19

```text
uv run pytest -q
95 passed, 1 skipped in 5.49s

uv run ruff check . --no-cache
All checks passed!

uv run ruff format --check .
95 files already formatted
```

Note: the shell reported `VIRTUAL_ENV=/Users/heecheonpark/Git/rag-agent/.venv` did not match this project `.venv`, and `uv` ignored it. The checks still completed successfully through this project's environment resolution.

## Known gaps / not complete yet

### Product/account lifecycle

- Generic SMTP auth email delivery is implemented for preview/public visitor verification and reset, but no live provider secrets or hosted smoke have been configured yet.
- Auth abuse protection is local/in-process by explicit Phase 1 decision; it is acceptable only for single-process public demos and is not a shared Redis/gateway limiter for multi-worker public deployment.
- No account deletion or profile management surface yet.
- Guest mode is deferred; no anonymous daily quota or restricted public-demo mode yet.

### Security and production hardening

- Needs explicit production security review.
- Needs shared/distributed rate limits before multi-worker public deployment; Phase 1 documents and tests the current single-process boundary.
- Credentialed CORS has explicit-origin configuration, but deployed frontend origins still need environment-specific verification.
- Needs secure cookie behavior verified behind the intended deployment/proxy setup.
- Needs live provider/host values and smoke execution; the deployment readiness runbook now defines provider records, privacy copy, rollback, and evidence bundle gates without storing secrets.

### Knowledge ingestion and retrieval

- PDF-first upload and deterministic text extraction now exist for text-based PDFs; scanned/encrypted/compressed PDFs are intentionally unsupported.
- No docx/HTML parsing pipeline yet.
- No background ingestion jobs yet.
- Embeddings are deterministic fixtures, not real embedding vectors.
- Retrieval is deterministic term scoring plus entity expansion, not pgvector similarity search.
- Entity extraction is deterministic/simple, not production NLP/LLM extraction.

### Agent/product behavior

- Product conversation runs support SSE progress streaming and incremental `answer_delta` assistant text events.
- Completed conversation runs can be refetched with persisted reply, route, and citations.
- `uv run python -m scripts.local_demo_seed` seeds a verified local demo user, text document, and extraction run for file-backed SQLite demos.
- `uv run python -m scripts.local_demo_smoke --base-url http://localhost:8000` verifies the seeded V1 API path without the frontend.
- Credentialed CORS can be enabled for exact frontend origins through `MY_AGENTS_CORS_ALLOWED_ORIGINS`.
- `docs/portfolio-chat-service/10-frontend-demo-runbook.md` documents local SQLite demo startup, dev auth outbox, cookie/CSRF expectations, frontend SSE flow, and run-detail refresh.
- `docs/portfolio-chat-service/11-v1-phase-0-contract-freeze-evidence-map.md` freezes the Phase 0 strict V1 DoD evidence matrix, backend OpenAPI inventory, frontend gate expectations, and known backend contract gaps by phase.
- `docs/portfolio-chat-service/12-public-demo-deployment-readiness.md` defines hosted preview/public smoke gates, provider/dependency decision records, privacy boundaries, rollback paths, and redacted evidence bundle schema.
- Current production graph is still one assistant/router path.
- Most route labels are capability metadata and response paths, not separate production specialist agents.
- Tool workflows beyond hosted web search are not implemented as production graph capabilities yet.
- No human-in-the-loop interrupts/checkpointed product workflow yet.

### Deployment/ops

- No deployment pipeline yet.
- Hosted preview/public DB migration execution is not proven yet; the readiness runbook records the migration evidence requirement and production remains user-gated.
- No observability backend/export yet.
- No frontend integration in this repository by design.

## Recommended next workflow

### Next milestone: strict V1 Phase 2 frontend gate, then Phase 3 citation/event contract

Backend Phase 2 PDF-first upload/ingestion is now implemented in the backend. The immediate cross-repo gate is for the separate frontend to verify or minimally adapt to `POST /documents/upload`, preserve the bodyless `/documents/{id}/ingest` flow, and consume backend OpenAPI fields without inventing alternate upload contracts.

Suggested order:

1. Frontend verifies the PDF upload contract from backend OpenAPI: multipart `POST /documents/upload` with `title`, optional `group_id`/`knowledge_base_id`, and `file`.
2. Frontend preserves the existing text seeded/demo path and bodyless `/documents/{id}/ingest` behavior.
3. Backend starts strict V1 Phase 3 after frontend gate feedback is recorded: richer citation provenance and event contract hardening.

Stop condition:

- Frontend upload gate passes or reports exact backend contract gaps.
- Backend remains green on pytest, Ruff check, Ruff format check, and diff check.
- Phase 3 does not begin by inventing frontend workarounds or expanding beyond citation/event contracts.

### Alternative next milestone: production RAG realism

Choose this instead if the priority is portfolio demo quality around documents and citations.

Suggested order:

1. Add file-upload metadata and text extraction boundaries.
2. Keep parsers local/deterministic first.
3. Replace embedding fixtures with a provider boundary that can be mocked in tests.
4. Add pgvector-backed ranking behind the existing permission filter.
5. Add ingestion status transitions for queued/running/completed/failed.
6. Add tests proving unauthorized chunks never enter ranking, context, citations, or events.

Stop condition:

- A demo can upload/ingest a realistic document and retrieve cited answers with permission safety preserved.

### Deferred idea: guest mode

Guest mode should not be part of the current v0 implementation. A future guest mode may allow unauthenticated users to try a limited demo flow with a small daily quota and no private document access. It should require anonymous identity/session tracking, rate limits, quota persistence, and strict permission separation before implementation. Prefer a seeded demo account or deterministic demo script for v0 portfolio demos.

## Completed milestone log

| Date | Milestone | Evidence |
| --- | --- | --- |
| 2026-05-20 | Public visitor auth email/provider boundary added. | `my_agents/auth/email.py`; `my_agents/auth/dependencies.py`; `my_agents/settings.py`; `tests/test_auth_email.py`; `tests/test_settings.py`; `.env.example`; README pair; `docs/portfolio-chat-service/10-frontend-demo-runbook.md`; `docs/portfolio-chat-service/12-public-demo-deployment-readiness.md`. |
| 2026-05-20 | Strict V1 Phase 2 backend PDF upload/ingestion added. | `my_agents/api/documents.py`; `my_agents/knowledge/pdf_uploads.py`; `my_agents/knowledge/extraction.py`; `my_agents/knowledge/models.py`; `alembic/versions/20260520_0004_pdf_upload_provenance.py`; `tests/test_knowledge_ingestion.py`; README pair. |
| 2026-05-20 | Strict V1 Phase 1 backend auth/session hardening added. | `my_agents/settings.py`; `tests/test_auth_api.py`; `tests/test_cors_api.py`; `tests/test_settings.py`; `docs/portfolio-chat-service/02-first-party-auth-sessions.md`; `docs/portfolio-chat-service/10-frontend-demo-runbook.md`. |
| 2026-05-20 | Strict V1 Phase 0 backend contract/evidence map added. | `docs/portfolio-chat-service/11-v1-phase-0-contract-freeze-evidence-map.md`; `docs/portfolio-chat-service/README.md`; `docs/implementation-tracking.md`. |
| 2026-05-20 | Public demo deployment readiness runbook added. | `docs/portfolio-chat-service/12-public-demo-deployment-readiness.md`; `docs/portfolio-chat-service/README.md`; `docs/implementation-tracking.md`. |
| 2026-05-20 | Backend-only V1 API smoke helper added for the seeded demo path. | `scripts/local_demo_smoke.py`; `tests/test_local_demo_smoke.py`; `docs/portfolio-chat-service/10-frontend-demo-runbook.md`. |
| 2026-05-20 | Local V1 demo seed helper added for verified user, text document, and extraction run. | `scripts/local_demo_seed.py`; `tests/test_local_demo_seed.py`; `docs/portfolio-chat-service/10-frontend-demo-runbook.md`. |
| 2026-05-20 | Refresh-safe run detail and gated local auth dev outbox added for frontend demo verification. | `tests/test_conversations_api.py`; `tests/test_permission_aware_rag.py`; `tests/test_auth_api.py`; `tests/test_migrations.py`. |
| 2026-05-20 | Credentialed frontend CORS configuration and local frontend demo runbook added. | `tests/test_cors_api.py`; `tests/test_settings.py`; `docs/portfolio-chat-service/10-frontend-demo-runbook.md`. |
| 2026-05-19 | Product conversation runs gained SSE progress and assistant-delta streaming plus frontend contract docs. | `tests/test_conversations_api.py`; `docs/portfolio-chat-service/09-http-streaming-frontend-contract.md`. |
| 2026-05-19 | Local auth abuse protection added for account lifecycle endpoints. | `92 passed, 1 skipped`; Ruff check/format pass; in-process `AuthAbuseProtector`; README/env/learning docs updated. |
| 2026-05-18 | Portfolio chat-service v0 backend foundation is in a strong test-backed state. | `84 passed, 1 skipped`; Ruff check/format pass. |
| 2026-05-18 | Portable implementation tracking added outside `.omx/`. | `docs/implementation-tracking.md` created and linked from root READMEs. |
| 2026-05-18 | Account lifecycle email verification and password reset implemented offline-first. | Local auth email boundary; verified-email login; password reset request/confirm; auth lifecycle token expiry/reuse tests. |

## Agent handoff checklist

Before starting a new workflow on any machine:

1. Read this file.
2. Check `git status --short`.
3. Run or inspect the latest relevant tests.
4. Update this file if your workflow changes completion, priorities, or known gaps.
5. Keep `.omx/` notes as local-only; do not rely on them for cross-machine continuity.
