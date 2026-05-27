# Implementation tracking

Last updated: 2026-05-27
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
| Demo-quality backend v0 | Hosted demo baseline proven | 94-96% | Thin end-to-end backend slice exists and now has a basic hosted path: Render backend, Vercel frontend CI/CD, Neon Postgres, Resend HTTP email from verified `my-agents.dev`, hosted signup/email verification, and deployment troubleshooting docs. Remaining demo risk is mostly PDF ingestion speed on small hosted resources and temporary diagnostic cleanup. |
| Production SaaS readiness | Early but hosted | 55-60% | Account lifecycle works in hosted demo mode with provider email, but production readiness still needs shared rate limits, durable queues/workers, ingestion performance hardening, automated smoke/migration gates, observability cleanup, and production security review. |
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
- Hosted Resend HTTP auth email delivery through verified `my-agents.dev`, with SMTP still available for hosts that allow it.
- Email verification token creation and `POST /auth/verify-email`.
- Verified-email login with app-owned opaque session cookie.
- CSRF token support for logout.
- `/auth/me` for current user lookup.
- Password reset request/confirm flow with non-enumerating request responses.
- Password reset revokes existing sessions.
- Password hashes and raw token hashes are not returned by API responses.
- Local in-process auth abuse protection covers repeated signup, bad login, reset request, and invalid lifecycle-token attempts.
- `MY_AGENTS_AUTH_SIGNUP_ENABLED=false` blocks new public-demo signups without changing existing login/session behavior.
- Provider-free guest access is env-gated by `MY_AGENTS_GUEST_ACCESS_ENABLED=false`
  by default. When enabled, public requests record an email without returning a
  code; operators issue one-time codes manually for explicit ephemeral guest
  identities with normal app session cookies, 24-hour expiry, one conversation,
  five prompts, and three document creates/uploads.

### Groups, documents, permissions

- Group creation/list/get.
- Member add/update flows.
- Document create/list/get.
- Document permission patching.
- Authorization service for read/write/manage/ingest decisions.

### Conversations and runs

- Server-owned conversations.
- Persisted user and assistant messages.
- Conversation run endpoint applies deterministic retrieval routing before invoking the current graph.
- SSE conversation-run stream emits redacted progress events, retrieval-route/answer-mode metadata, `answer_delta` assistant text chunks, and a final run response.
- Run summaries and run activity events are persisted and readable.
- Failure path records a failed run with redacted event metadata.

### Knowledge/RAG prototype

- Knowledge-base create/list.
- Text document ingestion remains compatible.
- Text-based upload path for PDF, Markdown, and plain text with safe metadata persistence; PDFs keep page provenance and FlateDecode text-stream fallback support. Hosted Render free-tier PDF ingestion is known to be slow for heavier PDFs.
- Deterministic chunks, provider-backed JSON embeddings (deterministic by default, OpenAI opt-in), entity mentions, and co-occurrence relationships.
- ContextForge now owns the conversation-run retrieval orchestration boundary through `my_agents/agents/context_forge/`, with deterministic query planning, source-boundary handoff, candidate fusion, deterministic default or optional cross-encoder reranking, high-recall context packing, redacted retrieval evidence, and opt-in Rich debug traces for role handoff messages.
- Retrieval candidate gathering includes authorized document title/source-filename metadata matching, so filename-only user references can find the matching uploaded document even when the filename is absent from chunk content.
- Ingestion stores structured knowledge entities for API endpoints, config keys, shell commands, error codes, and database table references with document/chunk/run/page/offset provenance.
- Deterministic retrieval routing supports `no_retrieval`, `retrieval_required`, `retrieval_optional`, and `clarification_required`; clarification runs now return `reply: ""` plus a language-neutral `clarification` contract for human-in-the-loop localization instead of static English prose.
- Permission-aware retrieval filters candidate chunks before ranking/expansion/composition.
- Structured enumeration prompts such as “list API endpoints in this document” can retrieve by extracted entity type instead of relying only on vector/keyword wording overlap.
- Broad personal-document fallback now retrieves recent authorized chunks for resume/profile/uploaded-document questions when exact term matching returns nothing.
- Authorized retrieved context plus `answer_mode` is passed into the general assistant graph/provider prompt, then citation-backed replies are persisted only when context is used.

### Persistence and migrations

- SQLAlchemy models cover auth, auth lifecycle tokens, sessions, groups, documents, knowledge artifacts, structured knowledge entities, conversations, runs, events, and citations.
- Alembic migrations cover the initial service schema, auth lifecycle, run detail refresh fields, PDF upload provenance fields, guest access state, retrieval-routing run metadata, pgvector chunk embeddings, async extraction-run progress fields, and structured knowledge entities.
- SQLite in-memory auto-create supports offline tests.
- Postgres/Neon readiness is documented, with external DB tests skipped unless configured.
- Hosted Render deployment uses Neon/Postgres and was verified through redacted runtime diagnostics.

### Documentation and learning support

- Bilingual root README pair: `README.md`, `README.en.md`.
- General assistant README pair under `my_agents/agents/general_assistant/`.
- ContextForge README pair under `my_agents/agents/context_forge/`.
- Product architecture notes under `docs/product-chat-service/en/`.
- Personal learning logs and agent-lab notes under `docs/learning/`.
- Simulated-agent candidate materials exist for future learning/practice ideas.

## Latest verification evidence

Last full local verification run: 2026-05-21

```text
uv run pytest -q
156 passed, 1 skipped in 6.54s

uv run ruff check . --no-cache
All checks passed!

uv run ruff format --check .
113 files already formatted

git diff --check
passed
```

The test harness sets `MY_AGENTS_ENV_FILE=` so a developer's local `.env` file cannot leak file-backed SQLite, cookie, or provider settings into offline verification.

## Known gaps / not complete yet

### Product/account lifecycle

- Generic SMTP auth email delivery is implemented for preview/public visitor verification and reset, but no live provider secrets or hosted smoke have been configured yet.
- Auth abuse protection is local/in-process by explicit Phase 1 decision; it is acceptable only for single-process public demos and is not a shared Redis/gateway limiter for multi-worker public deployment.
- No account deletion or profile management surface yet.
- Guest mode is implemented only as an env-gated public-demo path; no durable anonymous daily quota, self-service account deletion, or profile-management surface yet.

### Security and production hardening

- Needs explicit production security review.
- Needs shared/distributed rate limits before multi-worker public deployment; Phase 1 documents and tests the current single-process boundary.
- Credentialed CORS has explicit-origin configuration, but deployed frontend origins still need environment-specific verification.
- Needs secure cookie behavior verified behind the intended deployment/proxy setup.
- Needs live provider/host values and smoke execution; the deployment readiness runbook now defines provider records, privacy copy, rollback, and evidence bundle gates without storing secrets.

### Knowledge ingestion and retrieval

- Text-based upload and extraction supports text-based PDFs through `pypdf_text_v2`, Markdown through `utf8_markdown_v1`, and plain text through `utf8_text_v1`; simple PDFs keep a deterministic literal/FlateDecode stream fallback.
- Scanned/encrypted/image-only PDFs, OCR, docx, HTML, and CSV/JSON structural parsing are intentionally unsupported.
- Async ingestion progress is available through an additive in-process background endpoint (`POST /documents/{id}/ingest/async`) plus direct run polling; no durable external queue yet.
- Embeddings use a provider boundary: deterministic 32-dimensional lexical-hash vectors by default, or opt-in OpenAI embeddings through `langchain-openai` when `MY_AGENTS_EMBEDDING_MODE=openai`; Postgres chunks also store a pgvector `embedding_vector` through Alembic migration `20260521_0007`.
- Retrieval ranking is permission-first ContextForge orchestration over pgvector SQL vector search on Postgres, JSON cosine fallback for SQLite/tests, blended lexical score, entity expansion, structured entity retrieval, deterministic fusion/reranking, optional cross-encoder second-stage reranking over bounded authorized candidates, and a narrow personal-document fallback; LLM query rewrite, ANN/vector index tuning, production reranker packaging, and latency evals are still future work.
- ContextForge is the dedicated retrieval-agent service boundary. A future LangGraph `RetrievalGraph` remains deferred until role-node orchestration adds value beyond the current package-level roles; hard authorization should remain in `RetrievalService` even then.
- Entity extraction is deterministic regex/technical-term extraction, not production NLP/LLM extraction. Canonical entity creation is conflict-safe for concurrent async ingestion: names are pre-collected in stable order and inserted with dialect-aware `ON CONFLICT DO NOTHING` to avoid Postgres unique-index lock cycles.

### Agent/product behavior

- Product conversation runs support SSE progress streaming and incremental `answer_delta` assistant text events.
- Completed conversation runs can be refetched with persisted reply, route, and citations.
- `uv run python -m scripts.local_demo_seed` seeds a verified local demo user, text document, and extraction run for file-backed SQLite demos.
- `uv run python -m scripts.local_demo_smoke --base-url http://localhost:8000` verifies the seeded V1 API path without the frontend.
- Credentialed CORS can be enabled for exact frontend origins through `MY_AGENTS_CORS_ALLOWED_ORIGINS`.
- `docs/product-chat-service/en/10-frontend-demo-runbook.md` documents local SQLite demo startup, dev auth outbox, cookie/CSRF expectations, frontend SSE flow, and run-detail refresh.
- `docs/product-chat-service/en/11-v1-phase-0-contract-freeze-evidence-map.md` freezes the Phase 0 strict V1 DoD evidence matrix, backend OpenAPI inventory, frontend gate expectations, and known backend contract gaps by phase.
- `docs/product-chat-service/en/12-public-demo-deployment-readiness.md` defines hosted preview/public smoke gates, provider/dependency decision records, privacy boundaries, rollback paths, and redacted evidence bundle schema.
- Current production graph is still one assistant/router path.
- Most route labels are capability metadata and response paths, not separate production specialist agents.
- Tool workflows beyond hosted web search are not implemented as production graph capabilities yet.
- No human-in-the-loop interrupts/checkpointed product workflow yet.

### Deployment/ops

- Basic hosted deployment and CI/CD are configured: Render deploys the backend from Git, Vercel deploys the frontend production branch, Neon provides hosted Postgres, and Resend HTTP sends auth lifecycle email from `my-agents.dev`.
- Hosted signup -> email send -> frontend verification route has been proven after adding frontend `/verify-email` and `/password-reset` landing pages.
- Temporary `TEMP_DEPLOY_DIAG` logs remain and should be removed after a few more hosted smoke checks.
- Hosted preview/public DB migration execution is manually managed; the readiness runbook records the migration evidence requirement and production remains user-gated.
- No observability backend/export yet.
- Frontend integration lives in the separate frontend repository by design.

## Recommended next workflow

### Next milestone: hosted demo cleanup and smoke verification

The basic hosted path is now real enough to stabilize rather than keep adding features.
Render, Vercel, Neon, Resend HTTP, and `my-agents.dev` are wired for the public demo baseline.

Suggested order:

1. Promote the frontend verification-route fix to the Vercel production branch when ready.
2. Run hosted smoke: signup -> email verification -> login -> small document upload/ingest -> chat with citation.
3. Record any new issue in `docs/product-chat-service/en/15-deployment-troubleshooting-log.md`.
4. Remove temporary `TEMP_DEPLOY_DIAG` logs after hosted signup/login/chat smoke remains stable.
5. Treat Render free-tier PDF ingestion slowness as a known resource limitation; prefer small Markdown/plain-text/native-text PDFs for demo until ingestion moves to a worker or larger host.

Stop condition:

- Hosted smoke passes through auth, email verification, login, one document path, and one cited chat answer.
- Temporary diagnostics are removed or explicitly kept with a short removal date.
- Backend remains green on pytest, Ruff check, Ruff format check, and diff check.

### Follow-up milestone: strict KB-first frontend gate

Backend knowledge-base path work is KB-first. The cross-repo gate remains for the separate
frontend to keep create/select KB → upload/create document inside that KB → ingest inside that KB
→ choose KB sources for chat as the primary UX. The handoff artifact is
`docs/product-chat-service/en/12-knowledge-base-path-openapi-handoff.md`, with a filtered OpenAPI
contract JSON at `docs/product-chat-service/en/12-knowledge-base-path-openapi-handoff.json`.

### Alternative next milestone: production RAG realism

Choose this instead if the priority is public demo quality around documents and citations.

Suggested order:

1. [done] Add file-upload metadata and text extraction boundaries.
2. [done] Keep parsers local/deterministic first.
3. [done] Add pgvector-backed ranking behind the existing permission filter.
4. Cross-encoder reranking is now available only as a second-stage pass over top-k authorized candidates; next work is production packaging, latency budgets, and eval fixtures. Do not let a reranker see unauthorized chunks.
5. Add LLM query rewrite or context compression only after measuring retrieval quality.
6. [done] Add ingestion status transitions for queued/running/completed/failed.
7. Add tests proving unauthorized chunks never enter reranking, context, citations, or events.

Stop condition:

- A demo can upload/ingest a realistic document and retrieve cited answers with permission safety preserved.

### Public-demo guest mode boundary

Guest mode is now implemented only as a provider-free public-demo path. It is
disabled by default, creates explicit ephemeral guest identities, and enforces
single-session limits in backend state. It is not a durable anonymous quota
system, a multi-device guest account model, or a replacement for shared rate
limits.

## Completed milestone log

| Date | Milestone | Evidence |
| --- | --- | --- |
| 2026-05-27 | Basic hosted deployment and CI/CD baseline proven with Render backend, Vercel frontend, Neon Postgres, Resend HTTP email, verified `my-agents.dev`, and frontend auth email landing routes. | Backend commits `7a3b864`, `a6975cc`, `e455774`, `4f0b0b0`; frontend commit `7ade1aa`; `docs/product-chat-service/en/14-render-migration-and-rollback-notes.md`; `docs/product-chat-service/en/15-deployment-troubleshooting-log.md`; hosted logs showing `POST /auth/signup 201 Created` and `auth.email.resend_http.completed`. |
| 2026-05-26 | Email-gated guest requests added so the browser receives only an acknowledgement while operators issue one-time codes manually. | `my_agents/auth/service.py`; `my_agents/api/auth.py`; `my_agents/auth/models.py`; `alembic/versions/20260526_0016_guest_access_requests.py`; `scripts/issue_guest_access_code.py`; `tests/test_guest_access_api.py`; `docs/product-chat-service/en/02-first-party-auth-sessions.md`. |
| 2026-05-24 | Added ContextForge as the dedicated RAG retrieval-agent service boundary with structured entity extraction and endpoint enumeration retrieval. | `my_agents/agents/context_forge/`; `my_agents/knowledge/models.py`; `my_agents/knowledge/extraction.py`; `my_agents/knowledge/retrieval.py`; `my_agents/api/conversations/retrieval_context.py`; `my_agents/api/conversations/run_events.py`; `alembic/versions/20260524_0014_structured_knowledge_entities.py`; `tests/test_context_forge_contracts.py`; `tests/test_context_forge_structured_retrieval.py`; README pair; ContextForge README pair; `ROADMAP.md`. |
| 2026-05-24 | Added an OCR page cap for the Tesseract PDF fallback and moved lightweight text extractors before heavyweight Docling/OCR fallback, preventing image-heavy PDFs from monopolizing synchronous upload requests. | `my_agents/knowledge/pdf_uploads.py`; `my_agents/api/documents.py`; `my_agents/settings.py`; `.env.example`; `tests/test_knowledge_ingestion.py`; `tests/test_settings.py`; README pair. |
| 2026-05-24 | Added optional ContextForge cross-encoder reranking and Rich role-handoff debug traces behind env settings while preserving deterministic offline defaults. | `my_agents/agents/context_forge/reranking.py`; `my_agents/agents/context_forge/debug.py`; `my_agents/agents/context_forge/service.py`; `my_agents/settings.py`; `.env.example`; `tests/test_context_forge_reranking.py`; README pair; ContextForge README pair; `ROADMAP.md`. |
| 2026-05-21 | Added a local Docker pgvector helper for pulling DockerHub pgvector/Postgres, writing ignored backend env wiring, running Alembic, and executing the gated migration smoke. | `scripts/dev_pgvector.py`; `tests/test_dev_pgvector_script.py`; `.env.example`; README pair; `docs/product-chat-service/en/08-postgres-alembic-neon.md`. |
| 2026-05-22 | Fixed and documented the Postgres parallel-ingestion deadlock caused by shared entity names racing on `entities.name`. | `my_agents/knowledge/extraction.py`; `tests/test_knowledge_ingestion.py`; `docs/learning/06-parallel-ingestion-postgres-deadlock.md`; `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`; `docs/product-chat-service/en/08-postgres-alembic-neon.md`. |
| 2026-05-22 | Added additive async document ingestion with extraction-run progress fields, direct polling, in-process background execution, and permission-safe tests. | `my_agents/api/documents.py`; `my_agents/knowledge/extraction.py`; `my_agents/knowledge/models.py`; `my_agents/knowledge/schemas.py`; `alembic/versions/20260522_0008_async_extraction_progress.py`; `tests/test_knowledge_ingestion.py`; `tests/test_migrations.py`; README pair; `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`. |
| 2026-05-21 | Added Slice B pgvector chunk storage and permission-filtered SQL vector search with JSON/SQLite fallback. | `alembic/versions/20260521_0007_pgvector_chunk_embeddings.py`; `my_agents/knowledge/models.py`; `my_agents/knowledge/extraction.py`; `my_agents/knowledge/retrieval.py`; `tests/test_migrations.py`; `tests/test_permission_aware_rag.py`; README pair; `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`; `docs/product-chat-service/en/06-permission-aware-rag.md`; `docs/product-chat-service/en/08-postgres-alembic-neon.md`. |
| 2026-05-21 | Extended document upload beyond PDF to Markdown and plain text while preserving PDF provenance and retrieval behavior. | `my_agents/api/documents.py`; `my_agents/knowledge/uploads.py`; `tests/test_knowledge_ingestion.py`; README pair; `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`. |
| 2026-05-21 | Retrieval routing and answer-mode metadata added before graph invocation. | `my_agents/knowledge/routing.py`; `my_agents/knowledge/retrieval.py`; `my_agents/api/conversations.py`; `my_agents/conversations/models.py`; `alembic/versions/20260521_0006_retrieval_routing_metadata.py`; `tests/test_retrieval_routing.py`; `tests/test_conversations_api.py`; `tests/test_permission_aware_rag.py`; README pair; general assistant README pair; `docs/product-chat-service/en/06-permission-aware-rag.md`. |
| 2026-05-21 | PDF/text ingestion sophistication improved with `pypdf`, better chunking/entity extraction, and 32-d deterministic embedding fixtures. | `pyproject.toml`; `uv.lock`; `my_agents/knowledge/pdf_uploads.py`; `my_agents/knowledge/extraction.py`; `tests/test_knowledge_ingestion.py`; README pair; `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`. |
| 2026-05-21 | Added Slice A embedding provider boundary with deterministic default, optional OpenAI embeddings through `langchain-openai`, and permission-first JSON cosine retrieval ranking. | `my_agents/knowledge/embeddings.py`; `my_agents/knowledge/extraction.py`; `my_agents/knowledge/retrieval.py`; `.env.example`; README pair; permission/observability tests. |
| 2026-05-21 | Provider-free public-demo guest access added with one-time codes, guest sessions, and backend limits. | `my_agents/auth/service.py`; `my_agents/api/auth.py`; `my_agents/auth/guest_limits.py`; `alembic/versions/20260521_0005_guest_access.py`; `tests/test_guest_access_api.py`; README pair; `docs/product-chat-service/en/02-first-party-auth-sessions.md`. |
| 2026-05-21 | Generic container deployment path and backend signup disable switch added for public demos. | `Dockerfile`; `.dockerignore`; `my_agents/settings.py`; `my_agents/api/auth.py`; `tests/test_auth_api.py`; README pair; `docs/product-chat-service/en/13-generic-container-deployment-path.md`. |
| 2026-05-20 | PDF parser rejects corrupted binary text and supports FlateDecode resume retrieval smoke. | `my_agents/knowledge/pdf_uploads.py`; `tests/test_knowledge_ingestion.py`; README pair; `docs/product-chat-service/en/05-knowledge-ingestion-extraction.md`. |
| 2026-05-20 | Resume/profile RAG fallback added for broad personal-document questions. | `my_agents/knowledge/retrieval.py`; `my_agents/api/conversations.py`; `my_agents/agents/general_assistant/graph.py`; `my_agents/agents/general_assistant/responders.py`; `tests/test_permission_aware_rag.py`; `tests/test_responders.py`; README pair; general assistant README pair; `docs/learning/05-resume-rag-fallback-after-broad-personal-questions.md`. |
| 2026-05-20 | Public visitor auth email/provider boundary added. | `my_agents/auth/email.py`; `my_agents/auth/dependencies.py`; `my_agents/settings.py`; `tests/test_auth_email.py`; `tests/test_settings.py`; `.env.example`; README pair; `docs/product-chat-service/en/10-frontend-demo-runbook.md`; `docs/product-chat-service/en/12-public-demo-deployment-readiness.md`. |
| 2026-05-20 | Strict V1 Phase 2 backend PDF upload/ingestion added. | `my_agents/api/documents.py`; `my_agents/knowledge/pdf_uploads.py`; `my_agents/knowledge/extraction.py`; `my_agents/knowledge/models.py`; `alembic/versions/20260520_0004_pdf_upload_provenance.py`; `tests/test_knowledge_ingestion.py`; README pair. |
| 2026-05-20 | Strict V1 Phase 1 backend auth/session hardening added. | `my_agents/settings.py`; `tests/test_auth_api.py`; `tests/test_cors_api.py`; `tests/test_settings.py`; `docs/product-chat-service/en/02-first-party-auth-sessions.md`; `docs/product-chat-service/en/10-frontend-demo-runbook.md`. |
| 2026-05-20 | Strict V1 Phase 0 backend contract/evidence map added. | `docs/product-chat-service/en/11-v1-phase-0-contract-freeze-evidence-map.md`; `docs/product-chat-service/en/README.md`; `docs/implementation-tracking.md`. |
| 2026-05-20 | Public demo deployment readiness runbook added. | `docs/product-chat-service/en/12-public-demo-deployment-readiness.md`; `docs/product-chat-service/en/README.md`; `docs/implementation-tracking.md`. |
| 2026-05-20 | Backend-only V1 API smoke helper added for the seeded demo path. | `scripts/local_demo_smoke.py`; `tests/test_local_demo_smoke.py`; `docs/product-chat-service/en/10-frontend-demo-runbook.md`. |
| 2026-05-20 | Local V1 demo seed helper added for verified user, text document, and extraction run. | `scripts/local_demo_seed.py`; `tests/test_local_demo_seed.py`; `docs/product-chat-service/en/10-frontend-demo-runbook.md`. |
| 2026-05-20 | Refresh-safe run detail and gated local auth dev outbox added for frontend demo verification. | `tests/test_conversations_api.py`; `tests/test_permission_aware_rag.py`; `tests/test_auth_api.py`; `tests/test_migrations.py`. |
| 2026-05-20 | Credentialed frontend CORS configuration and local frontend demo runbook added. | `tests/test_cors_api.py`; `tests/test_settings.py`; `docs/product-chat-service/en/10-frontend-demo-runbook.md`. |
| 2026-05-19 | Product conversation runs gained SSE progress and assistant-delta streaming plus frontend contract docs. | `tests/test_conversations_api.py`; `docs/product-chat-service/en/09-http-streaming-frontend-contract.md`. |
| 2026-05-19 | Local auth abuse protection added for account lifecycle endpoints. | `92 passed, 1 skipped`; Ruff check/format pass; in-process `AuthAbuseProtector`; README/env/learning docs updated. |
| 2026-05-18 | Product chat-service v0 backend foundation is in a strong test-backed state. | `84 passed, 1 skipped`; Ruff check/format pass. |
| 2026-05-18 | Portable implementation tracking added outside `.omx/`. | `docs/implementation-tracking.md` created and linked from root READMEs. |
| 2026-05-18 | Account lifecycle email verification and password reset implemented offline-first. | Local auth email boundary; verified-email login; password reset request/confirm; auth lifecycle token expiry/reuse tests. |

## Agent handoff checklist

Before starting a new workflow on any machine:

1. Read this file.
2. Check `git status --short`.
3. Run or inspect the latest relevant tests.
4. Update this file if your workflow changes completion, priorities, or known gaps.
5. Keep `.omx/` notes as local-only; do not rely on them for cross-machine continuity.
