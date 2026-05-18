# Implementation tracking

Last updated: 2026-05-18
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
| Portfolio-quality backend v0 | In progress, strong foundation | 78-82% | Thin end-to-end backend slice exists; auth lifecycle is now more product-shaped and test-backed. |
| Production SaaS readiness | Early | 40-45% | Account lifecycle improved; still needs real email provider, abuse protection, deployment hardening, production ingestion, and ops work. |
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
- Run summaries and run activity events are persisted and readable.
- Failure path records a failed run with redacted event metadata.

### Knowledge/RAG prototype

- Knowledge-base create/list.
- Text document ingestion.
- Deterministic chunks, embedding fixtures, entity mentions, and co-occurrence relationships.
- Permission-aware retrieval filters candidate chunks before ranking/expansion/composition.
- Citation-backed replies for retrieved authorized context.

### Persistence and migrations

- SQLAlchemy models cover auth, auth lifecycle tokens, sessions, groups, documents, knowledge artifacts, conversations, runs, events, and citations.
- Alembic migration exists for the initial service schema.
- SQLite in-memory auto-create supports offline tests.
- Postgres/Neon readiness is documented, with external DB tests skipped unless configured.

### Documentation and learning support

- Bilingual root README pair: `README.md`, `README.en.md`.
- General assistant README pair under `my_agents/agents/general_assistant/`.
- Portfolio architecture notes under `docs/portfolio-chat-service/`.
- Personal learning logs and agent-lab notes under `docs/learning/`.
- Simulated-agent candidate materials exist for future learning/practice ideas.

## Latest verification evidence

Last full local verification run: 2026-05-18

```text
uv run pytest -q
87 passed, 1 skipped in 3.14s

uv run ruff check . --no-cache
All checks passed!

uv run ruff format --check .
88 files already formatted
```

Note: the shell reported `VIRTUAL_ENV=/Users/heecheonpark/Git/rag-agent/.venv` did not match this project `.venv`, and `uv` ignored it. The checks still completed successfully through this project's environment resolution.

## Known gaps / not complete yet

### Product/account lifecycle

- Real outbound email provider integration is not implemented yet; v0 uses an offline local sender boundary.
- No signup/login/password-reset rate limiting yet.
- No account deletion or profile management surface yet.
- Guest mode is deferred; no anonymous daily quota or restricted public-demo mode yet.

### Security and production hardening

- Needs explicit production security review.
- Needs rate limits and abuse protection.
- Needs CORS/deployment configuration review for a real frontend origin.
- Needs secure cookie behavior verified behind the intended deployment/proxy setup.
- Needs secrets/deployment runbook outside local `.env` usage.

### Knowledge ingestion and retrieval

- No real file upload pipeline yet.
- No PDF/docx/HTML parsing pipeline yet.
- No background ingestion jobs yet.
- Embeddings are deterministic fixtures, not real embedding vectors.
- Retrieval is deterministic term scoring plus entity expansion, not pgvector similarity search.
- Entity extraction is deterministic/simple, not production NLP/LLM extraction.

### Agent/product behavior

- Product conversation runs are not streaming yet.
- Current production graph is still one assistant/router path.
- Most route labels are capability metadata and response paths, not separate production specialist agents.
- Tool workflows beyond hosted web search are not implemented as production graph capabilities yet.
- No human-in-the-loop interrupts/checkpointed product workflow yet.

### Deployment/ops

- No deployment pipeline yet.
- No production DB migration runbook beyond local docs.
- No observability backend/export yet.
- No frontend integration in this repository by design.

## Recommended next workflow

### Next milestone: auth abuse protection and provider-readiness

Goal: harden the new auth lifecycle before adding anonymous guest mode or real provider cost.

Suggested order:

1. Add a simple rate-limit/attempt-tracking boundary for signup, login, verification, and password reset.
2. Keep the first implementation local/testable; avoid external Redis or hosted services unless deployment requires them.
3. Add tests for repeated bad login, repeated reset request, and token brute-force protection.
4. Draft provider-readiness docs for Resend/AWS SES without implementing live sending yet.
5. Keep guest mode deferred until authenticated flows have abuse protection.

Stop condition:

- Auth abuse tests pass offline.
- Docs clearly separate local sender, future real email provider, and deferred guest mode.
- Existing deterministic assistant/conversation tests still pass.

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
