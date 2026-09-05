# Implementation tracking

Last updated: 2026-09-05
Status owner: repo-tracked source of truth for cross-machine agent handoff

This is the portable implementation/status record, independent of machine-local agent sessions.
Start here before re-discovering project status from the codebase.

Use this file to answer: **"What should we do next?"** without first re-reading the whole codebase. For detailed backlog coverage, use [`../ROADMAP.md`](../ROADMAP.md) as the companion checklist.

## Source-of-truth contract

- `docs/implementation-tracking.md` is the sole mutable status authority: current status, latest verification, known gaps, recommended sequence, and the shipped/completed milestone index.
- `ROADMAP.md` is the detailed roadmap/checklist backlog: broader v1 scope, deferred items, and definition of done.
- If both files mention the same item, this file decides current priority and freshness; update `ROADMAP.md` afterward so the checklist does not drift.
- `docs/README.md` is the documentation router and full lifecycle policy. Substantive terminal initiative detail moves to `docs/completed/<initiative-slug>.md`; those records preserve history but never own current status.
- Machine-local agent sessions and temporary handoffs do not override this portable baseline.

## How to use this file

- Start here when the user asks what to do next, what is incomplete, or what changed since another machine/session.
- Update this file when a workflow meaningfully changes project completion, next priorities, or known gaps.
- Keep it factual: record implemented behavior, verification evidence, and remaining risk separately.
- Prefer short entries with links to code/docs/tests instead of long narrative.
- Treat uncommitted work as active, feature-branch work as implemented but not integrated, merged and verified product behavior as shipped, and hosted behavior as production-verified only after a deployment smoke.
- When work becomes terminal, update current behavior docs, compact substantive history under `docs/completed/`, replace active detail with a concise checked index row, remove stale gap/priority language, and update `ROADMAP.md` in the same change.
- Existing historical material is grandfathered and may be compacted incrementally; do not perform a large archive migration while handling an unrelated task.
- Do not use this file for secrets, local `.env` values, or machine-specific runtime state.

## Current overall status

As of 2026-09-05, the implemented baseline includes the General Assistant/RAG Agent split,
permission-first hybrid and bounded full-document retrieval, selective citation attribution,
reasoning summaries, PostgreSQL-backed HITL, and optional document-workspace APIs. The released
frontend includes source-choice interactions, workspace controls, and Mermaid rendering.
The owner confirmed immediate authenticated chat recovery after the pool hotfix; complete
feature-level/longer-idle production verification and stronger automated release gates remain open.
See the recommended workflow and completion index for current priorities and delivered scope.

### Historical maturity estimates

The estimates below are retained from the earlier product-review snapshot. They have not been
recomputed for the September release and are not current completion metrics or release gates.

| Scope | Status | Completion estimate | Notes |
| --- | --- | ---: | --- |
| Demo-quality product preview | Controlled-alpha ready after latest deploy smoke | 96-98% | Backend and separate frontend now cover the core product loop: account signup with nickname, invite-only groups, manager-only member rosters, personal/group KBs, hidden group-upload staging, publish-request review with readable source previews, route-addressable group management, document ingestion, cited chat, and redacted agent trace/events. Remaining preview risk is mostly hosted redeploy/migration evidence after the latest group/nickname/publish changes, ingestion-worker smoke, and small-host reliability. |
| Production SaaS readiness | Early but hosted | 60-65% | Account lifecycle and group knowledge workflows are good enough for a narrow trusted preview, but production readiness still needs shared rate limits, account deletion/profile management, durable queue/stale-run recovery, hosted ingestion-worker smoke, automated smoke/migration gates, observability cleanup, and production security review. |
| Full AI agents product vision | Early/mid | 35-45% | Current production graph is one general assistant controller that can invoke the RAG Agent retrieval boundary; richer non-RAG tool workflows, scoped instructions, and multi-agent production orchestration remain future milestones. |
| Learning/practice simulated agents | Moved out | Ongoing in separate repo | Simulated-agent practice code now lives in `~/Git/Playground/langgraph-playground`; this repo stays focused on production API/CLI surfaces. |

## Product review verdict — 2026-06-14

It is worth inviting a small number of trusted people to try the product **as a controlled alpha / product preview**, not as a broad public launch. The current version is coherent enough to demonstrate the intended product surface: sign up with a human nickname, create or join invite-only groups, upload small supported documents, request/approve group sharing, ask cited questions, and inspect redacted agent activity.

Recommended framing for testers:

- invite people who can tolerate rough edges and give product feedback;
- ask them to use small Markdown/plain-text/native-text PDF files first;
- tell them not to upload sensitive, regulated, or irreplaceable source files yet;
- keep owner/operator support available for account approval, invite issues, and ingestion failures;
- run a fresh hosted smoke after each deploy before sending the link.

Do **not** position it as production-ready or broadly self-serve yet. The main blockers are operational rather than product-shape blockers: latest migrations/OpenAPI/frontend deployment smoke, external ingestion-worker evidence, account/profile lifecycle gaps, shared rate limiting, production security review, and durable worker/queue recovery.

## Implemented and verified baseline

### Backend/API foundation

- FastAPI app factory and route assembly: `my_agents/api/__init__.py`
- Health endpoint: `GET /health`
- Legacy/dev assistant smoke endpoint: `POST /assistant/chat`
- Product conversation-run surface under `/conversations`
- Auth, groups, documents, knowledge bases, and run events are registered routes.
- HTTP and validation failures preserve `detail` and add stable machine-readable `code`
  values, with specific frontend localization keys for high-value auth, guest-limit,
  upload-size, publish-review, and run-lifecycle failures.
- Opt-in internal Prometheus timing endpoint: `GET /metrics` is exposed only when
  `MY_AGENTS_METRICS_ENABLED=true`.

### Assistant graph

- Production-surface assistant graph lives in `my_agents/agents/general_assistant/`.
- Uses LangGraph `StateGraph` with explicit classification and response nodes.
- Classification is deterministic.
- Product graph now runs a source-selection gate before private KB retrieval; OpenAI mode can use a thin multilingual LLM decision, while deterministic mode keeps an offline fallback and explicit no-retrieval result for bypassed turns.
- After private-knowledge delegation, the RAG Agent now uses fixed `gpt-5.6-luna` standard/low tool selection to choose focused authorized chunk search or comprehensive document reading. Deterministic mode, invalid tool output, and provider failure retain an offline semantic fallback. The comprehensive branch resolves one authorized personal/group document, prepares bounded coverage, recalls memory, and answers through `respond_full_document`; normal questions stay on focused retrieval.
- Graph version `general-assistant-checkpoint-v2` keeps only compact IDs, retrieval snapshots, offsets, and coverage metadata in persisted execution state; raw full-document text is re-read inside the response node and is not checkpointed.
- OpenAI-backed response generation uses `langchain-openai` / `ChatOpenAI` by default.
- Deterministic mode remains available for tests and offline smoke checks.
- Hosted web search is exposed at the OpenAI response-provider boundary for both `general_assistant` and `research_helper`; the provider prompt, not app-side language-specific keyword hints, decides when the model should call it.
- Opt-in long-term memory V1 is implemented as a Product DB-backed governance/runtime scaffold: settings, memory CRUD, suggest-confirm lifecycle, policy gates, source provenance, source staleness, relevance-minimized context injection, conflict guidance, and redacted run snapshots. The LangGraph-native migration target is documented in `docs/product-chat-service/en/19-langgraph-native-memory-migration.md`.

### Auth/session foundation

- Email/password signup, plus invitation-token signup for no-account invitees that asks only for nickname/password.
- Nickname signup/member-roster contract is implemented as display-only and duplicate-allowed; deployment handoff should refresh hosted OpenAPI after migration/API/frontend updates land together.
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
  by default. When enabled, public requests never return a code; they either email
  one automatically or enter manual approval according to
  `MY_AGENTS_GUEST_CODE_AUTO_APPROVAL`. `GET /auth/guest/policy` exposes the
  effective public policy. Repo defaults are 24-hour expiry, three conversations,
  twenty prompts, and five document creates/uploads, while deployments may override them.
- `users.user_type` distinguishes `normal`, `root`, and `system` platform privilege
  from registered/guest `account_type`; auth responses expose read-only `user_type`
  and `can_manage_system_knowledge` only for root/system managers, and mutation is
  script-only through `scripts.set_user_type` /
  `scripts.ops account set-user-type`.

### Groups, documents, permissions

- Group creation/list/get.
- Invite-only group membership lifecycle: owner/admin email invitations, opaque token acceptance, no-account invite-token signup, pending invitation management, manager-only accepted-member roster, and non-creating role updates.
- Nickname roster extension keeps member emails out of manager-only rosters and keeps role updates keyed by `user_id`; duplicate nicknames remain display-only.
- Group knowledge publish-request workflow supports personal-KB publication and single-document copy publication into group KBs, with owner/admin approve/reject and source-preview data for review.
- Hidden `team_upload_staging` KBs allow group document uploads to stay private until approval copies the source into the target group KB.
- Document create/list/get.
- Document permission patching.
- Authorization service for read/write/manage/ingest decisions.

### Conversations and runs

- Server-owned conversations, listed newest-first for frontend sidebar recency.
- Persisted user and assistant messages.
- Conversation run endpoint applies deterministic retrieval routing before invoking the current graph.
- SSE conversation-run stream emits redacted progress events, retrieval-route/answer-mode metadata, compact localized ko/en `agent_trace` steps, real ordinary-provider `answer_delta` chunks, and a final run response aggregated from those same chunks.
- Assistant-message replay now also supports an SSE stream path, so regeneration can show live progress and answer deltas while preserving the old transcript unless the replay completes successfully.
- Run summaries and run activity events are persisted and readable.
- Persisted activity events use a closed, `event_type`-discriminated OpenAPI union;
  every event payload and nested `agent_trace.evidence` object passes through a typed
  allowlist before leaving the API.
- Completed comprehensive-document runs expose refresh-safe `document_coverage` in run responses/details and a redacted `full_document_read` persisted/SSE event with document metadata, half-open character offsets, total length, and latency. Neither contract includes raw document text or the internal continuation cursor.
- Run, resume, and replay SSE keep `retrieval_completed` as a single progress event, but derive terminal citations, insufficiency, and coverage from the final graph retrieval state after the full-document response-node re-read. A TOCTOU change therefore fails closed with null coverage and no stale full-document evidence.
- Completed attributed runs expose the complete user-visible consulted evidence through nullable
  `consulted_sources` and the conservative answer-supported subset through `citations`. Both
  arrays reuse persisted evidence IDs. Legacy runs return `consulted_sources=null`; new runs use
  a list, including `[]` for a genuinely empty consulted set.
- Failure path records a failed run with redacted event metadata.
- Opt-in document-selection HITL emits V2 for new runs while retaining V1 waiting-checkpoint resume compatibility. Exact filenames resolve automatically; otherwise the backend exposes a ranked shortlist of at most five authorized documents, accepts two bounded human refinements on the same run, and opens broad browsing only after exhaustion. Product DB refresh refilters current authorization and preserves one original expiry across fresh per-attempt UUIDs.
- Document-selection options are a narrower user-control boundary than retrieval: personal/group documents may be chosen, while ambient system knowledge remains automatically injected, hidden from the option list, and invalid as a submitted resume selection.
- Conversation-run timing histograms cover sync and streaming outcomes for internal
  performance review when metrics are enabled.

### Knowledge/RAG prototype

- Knowledge-base create/list.
- Text document ingestion remains compatible.
- Upload path for PDF, Markdown, plain text, `.xlsx`, `.pptx`, and DOCX-only `.docx` with safe metadata persistence; PDFs keep page provenance, Office uploads keep Markdown parse artifacts where available, native-text PDF happy paths skip duplicate pypdf pre-classification after PyMuPDF passes the existing quality gate, and hosted ingestion can run through an external worker so heavy parser/embedding/indexing work no longer has to share the web request process.
- Deterministic chunks, provider-backed JSON embeddings (deterministic by default, OpenAI opt-in), entity mentions, and co-occurrence relationships.
- RAG Agent now owns the assistant-facing conversation-run retrieval boundary through `my_agents/agents/rag_agent/retrieval.py`; `general_assistant` invokes it inside the graph before memory/answer nodes only when the source-selection gate chooses private knowledge-base retrieval.
- The same RAG Agent runtime now owns typed `resolve_full_document_target` and `read_full_document_range` seams for explicit whole-document tasks. Resolution and every read reuse the permission-first user-selectable-document filter, including owner/group/explicit-document grants while excluding ambient system KB documents.
- For explicit comprehensive-document intent, normalized extracted text at or below 24,000 characters is one complete read. Larger documents currently contribute only characters `[0, 12000)` and receive a localized partial-review disclosure; overlapping authorized chunks remain the consulted provenance source.
- ContextForge remains the delegated permission-first retrieval engine behind that boundary, with a thin LangGraph RetrievalGraph wrapper over deterministic query planning, source-boundary handoff, independent vector and request-local `BM25Okapi` lexical rankings, `chunk_id`-keyed RRF fusion (`k=60`), deterministic default or optional lazily loaded cross-encoder reranking, high-recall context packing, redacted retrieval evidence, and opt-in Rich debug traces for role handoff messages.
- Retrieval candidate gathering includes authorized document title/source-filename metadata matching, so filename-only user references can find the matching uploaded document even when the filename is absent from chunk content.
- Ingestion stores structured knowledge entities for API endpoints, config keys, shell commands, error codes, and database table references with document/chunk/run/page/offset provenance.
- Deterministic retrieval routing supports `no_retrieval`, `retrieval_required`, `retrieval_optional`, and `clarification_required`. Non-interrupted clarification replies retain visible text; checkpoint-backed HITL instead returns explicit `waiting_for_input` interaction state, not a completed assistant reply.
- Permission-aware retrieval filters candidate chunks before ranking/expansion/composition.
- Structured enumeration prompts such as “list API endpoints in this document” can retrieve by extracted entity type instead of relying only on vector/keyword wording overlap.
- Broad personal-document fallback now retrieves recent authorized chunks for resume/profile/uploaded-document questions when exact term matching returns nothing.
- Authorized retrieved context plus `answer_mode` is now produced by the RAG Agent call inside the general assistant graph; the `rag_agent` contract graph still verifies compact trace stages and grounding checks before citation-backed replies are persisted.
- System knowledge bases (`scope=system`) are manager-only for CRUD/document
  operations but ambiently included in authenticated chat retrieval, including guest
  sessions. Authorized answerable snippet text reaches the model; ambient system source
  identifiers and provenance remain internal rather than being included in provider context,
  while public run/event/citation responses omit system KB counts, IDs, filenames, snippets,
  and citations.
- ContextForge, retrieval phases, embedding provider calls, reranker calls, and
  graph invocation timings are recorded as opt-in internal Prometheus histograms.

### Persistence and migrations

- SQLAlchemy models cover auth, auth lifecycle tokens, sessions, groups, documents, knowledge artifacts, structured knowledge entities, conversations, runs, events, and citations.
- Alembic migrations cover the initial service schema, auth lifecycle, run detail refresh fields, PDF upload provenance fields, guest access state, retrieval-routing run metadata, pgvector chunk embeddings, async extraction-run progress fields, structured knowledge entities, the `users.user_type` privilege column, and nullable citation-attribution markers without legacy backfill.
- SQLite in-memory auto-create supports offline tests.
- Postgres/Neon readiness is documented, with external DB tests skipped unless configured.
- Hosted Render deployment uses Neon/Postgres and was verified through redacted runtime diagnostics.

### Documentation and learning support

- Bilingual root README pair: `README.md`, `README.en.md`.
- General assistant README pair under `my_agents/agents/general_assistant/`.
- ContextForge README pair under `my_agents/agents/context_forge/`.
- RAG Agent workflow README pair under `my_agents/agents/rag_agent/`.
- Product architecture notes under `docs/product-chat-service/en/`, including the G001
  architecture report at
  `docs/product-chat-service/en/22-general-assistant-rag-agent-architecture-change-report.md`.
- Personal backend learning logs and my-agents-specific project notes under `docs/learning/`.
- Reusable LangGraph practice conventions, pattern docs, and runnable simulated-agent implementations now live in `~/Git/Playground/langgraph-playground`.

## Latest verification evidence

LangGraph stale-connection hotfix on 2026-09-05:

- Production chat exposed a failed initial checkpoint read after an SSL connection closed.
  The shared Psycopg pool now checks connections at checkout; it does not replay graph work.
- Before the fix, local server-side disconnection failed both checkpoint and Store reads.
  After the fix, all **4 PostgreSQL persistence tests passed**, including restart/resume.
- Full offline suite: **585 passed, 14 gated skips, 11 warnings**; Ruff lint/format passed.
  No schema, dependency, or environment changes. Hotfix `e62d45a` was pushed to main and
  fast-forwarded into develop; both remote branches were verified at that commit.
- The owner reported successful authenticated chat after the hotfix. This confirms immediate
  recovery, not the longer-idle production case; that remains unverified. Health/401 probes
  do not exercise checkpoint reads and are not evidence that this failure path is fixed.
- [Debugging note and recovery boundaries](./learning/project-notes/langgraph-stale-connections.md).
- The owner confirmed Render's pre-deploy command is Alembic-only. The stronger framework
  setup/status/reconciliation chain and isolated regression release gate are documented
  recommendations, not configured automation. See [the Render runbook](./product-chat-service/en/14-render-migration-and-rollback-notes.md#production-pre-deploy-guardrail).

True checkpoint-resume streaming on 2026-08-26:

- Reproduced the UX regression in source: `/resume/stream` executed the synchronous checkpoint
  resume to completion before its first SSE yield, then split the finished reply into artificial
  deltas. It emitted no live `run_resumed`, retrieval, or graph progress.
- Sync and SSE resume now share one authorization/atomic-claim helper. SSE emits `run_resumed`
  first and drives the checkpoint with LangGraph `messages` plus `updates`, with real progress,
  real provider deltas when available, and cancellation checks between stream steps.
- The regression test failed before the fix because `answer_delta` was the first event. It now
  requires `run_resumed` first and both `retrieval_completed` and `graph_invoked` before the first
  answer delta.
- The final backend suite passed: **534 passed, 2 skipped, 11 warnings** in 56.36s. Ruff lint,
  Ruff format-check, and `git diff --check` passed.

Consulted-source and answer-supported citation split on 2026-08-25:

- Added nullable `ConversationRunResponse.consulted_sources` as the complete user-visible
  consulted superset while `citations` is now the conservative post-hoc answer-supported subset.
  Overlapping entries reuse the same persisted evidence row and therefore the same `id` and
  `chunk_id`.
- Added nullable Product DB attribution markers without legacy backfill. Legacy runs serialize
  `consulted_sources=null`; attributed runs serialize a list, including `[]` for a genuinely
  empty set. Sync, normal SSE, resume SSE, replay, and run-detail refresh share the serializer.
- Generated OpenAPI declares `consulted_sources` as nullable array items referencing
  `CitationResponse`; the exact contract is covered by `tests/test_kb_openapi_contract.py`.
- `CitationResponse` also exposes nullable `document_title` and `knowledge_base_name` so the
  product UI can collapse chunk rows into one human-readable entry per document, optionally
  aggregating pages while hiding internal IDs and snippets.
- The final offline suite passed: **534 passed, 2 skipped, 11 warnings** in 53.41s. Ruff lint,
  Ruff format-check, and `git diff --check` passed on the final tree.

Many-chunk full-document provenance fix on 2026-08-25:

- Reproduced the reported local document as an authorized, completed 16,600-character Markdown extraction with 190 valid current chunks. The former 100-chunk safety cutoff converted that valid provenance into zero chunks and triggered insufficient evidence after document selection.
- Full-document reads now validate at most 2,000 overlapping chunks and retain at most 100 evenly distributed provenance chunks, including the first and last. The exact local document now reports complete coverage with 100 unique consulted-source records spanning ordinals 0 through 189.
- Added a 190-section Markdown service/API regression and deterministic coverage for the exact Korean `문서를 모두 읽고 내용을 요약해줘` intent. A credential-free graph invocation against the local document selected `read_authorized_document_comprehensively`, returned `document_grounded`/`complete`, produced 100 consulted-source records, and did not return the insufficient-evidence fallback.
- The pre-attribution full-document suite passed: **528 passed, 2 skipped, 11 warnings** in 54.28s. Ruff lint, Ruff format-check, and `git diff --check` passed on that tree.

Full-document retrieval vertical-slice implementation on 2026-08-24:

- Added Luna and deterministic Korean/English semantic-intent fixtures, complete/partial range behavior, permission and exact-target replay checks, ambiguous document-selection resume, refresh-safe coverage, SSE disclosure, citation-range checks, and a checkpoint raw-body regression.
- Added fixed Luna standard/low model-policy, strict required tool binding, natural Korean regression, focused negative controls, invalid-output fallback, provider-timeout fallback, and false-positive prevention in `tests/test_rag_agent_tool_selection.py`.
- `uv run pytest -q tests/test_rag_agent_tool_selection.py tests/test_rag_agent_contracts.py tests/test_graph.py tests/test_full_document_retrieval.py tests/test_retrieval_gate.py tests/test_langgraph_persistence.py tests/test_agent_event_contract.py` passed: **57 passed, 1 skipped, 5 warnings** in 2.42s.
- One credentialed Luna smoke selected `read_authorized_document_comprehensively` for the original natural Korean prompt without reading document content during planning.
- The final reconciled offline suite passed after moving all comprehensive intent ownership out of `classify_request`: **525 passed, 3 skipped, 11 warnings** in 82.10s. Ruff lint, Ruff format-check, and `git diff --check` also passed on the final tree.

Guest policy and email delivery verification on 2026-08-09:

- The automatic-approval request path, using the production Resend credential and
  verified `my-agents.dev` sender against Resend's `delivered@resend.dev` test
  recipient, returned `200 accepted`; Resend reported the message as `delivered`.
- A separate request through the production Vercel BFF returned `200 accepted`, but
  produced no matching Resend event. The checked production config snapshot also has
  `MY_AGENTS_GUEST_CODE_AUTO_APPROVAL=false`, so hosted automatic delivery was not
  active at verification time. Enable the flag in the hosted service and redeploy or
  restart before the frontend promises immediate code delivery.
- The production BFF currently rejects `GET /auth/guest/policy` as a path outside its
  allowlist; add the path when the backend contract is deployed.

Frontend contract hardening on 2026-08-09:

```text
uv run pytest -q
474 passed, 2 skipped, 9 warnings in 70.70s

uv run ruff check . --no-cache
All checks passed!

uv run ruff format --check .
218 files already formatted

git diff --check
passed
```

Internal timing metrics implementation on 2026-06-16:

```text
uv run pytest -q tests/test_metrics.py tests/test_settings.py
35 passed, 5 warnings in 3.01s

uv run pytest -q tests/test_metrics.py tests/test_settings.py tests/test_agent_observability_evals.py tests/test_context_forge_contracts.py tests/test_context_forge_reranking.py tests/test_permission_aware_rag.py tests/test_conversations_api.py
108 passed, 5 warnings in 13.46s

uv run ruff check . --no-cache
All checks passed!

uv run ruff format --check .
201 files already formatted

uv run pytest -q
415 passed, 1 skipped, 9 warnings in 47.00s

git diff --check
passed
```

Product status docs refresh on 2026-06-14:

```text
uv run pytest -q
371 passed, 2 skipped, 9 warnings in 38.29s

uv run ruff check . --no-cache
All checks passed

uv run ruff format --check .
189 files already formatted

git diff --check
passed
```

Memory architecture / ContextForge graph review follow-up on 2026-06-10:

```text
uv run pytest -q tests/test_graph.py tests/test_conversations_api.py tests/test_context_forge_contracts.py tests/test_context_forge_reranking.py tests/test_context_forge_structured_retrieval.py tests/test_permission_aware_rag.py tests/test_rag_agent_contracts.py
85 passed, 5 warnings

uv run pytest -q
349 passed, 1 skipped, 9 warnings

uv run ruff format --check .
183 files already formatted

uv run ruff check . --no-cache
All checks passed

git diff --check
passed
```

Review follow-up on 2026-06-10: graph-owned memory recall now preserves internal redacted memory-source audit snapshots for failed runs when recall completed before a later provider failure, while frontend-visible run events expose only memory counts/categories/provenance types. Documentation now states that ContextForge `RetrievalGraph` is already a thin runtime wrapper and must not be checkpointer-persisted as raw retrieval state.

Previous full local verification run: 2026-05-30

```text
uv run pytest -q
278 passed, 2 skipped, 7 warnings in 31.53s

uv run ruff check . --no-cache
All checks passed!

uv run ruff format --check .
173 files already formatted

git diff --check
passed
```

Additional docs/status-review checks on 2026-05-30:

```text
MY_AGENTS_ENV_FILE= MY_AGENTS_RESPONSE_MODE=deterministic uv run python - <<'PY'
from fastapi.testclient import TestClient
from my_agents.api import create_app
client = TestClient(create_app())
print(client.get("/health").status_code)
print(client.post("/assistant/chat", json={"message":"Plan my next backend milestone","history":[]}).status_code)
PY
# health 200; assistant chat 200

git grep -n -I -E 'sk-[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]]+:[^[:space:]@]+@|RESEND_API_KEY=.*re_|OPENAI_API_KEY=sk-|BEGIN (RSA|OPENSSH|PRIVATE) KEY' -- . ':!uv.lock'
# only safe placeholder examples in tracked docs/env examples
```

The test harness sets `MY_AGENTS_ENV_FILE=` so a developer's local `.env` file cannot leak file-backed SQLite, cookie, or provider settings into offline verification.

Agentic RAG workflow evidence on 2026-06-06:

- Added `my_agents/agents/rag_agent/` as the RAG Agent contract/verifier layer for the V1 agentic RAG workflow. It now includes a bounded Luna retrieval-tool selector while keeping deterministic trace verification and offline fallback. The public retrieval boundary is RAG Agent, ContextForge remains the internal focused-retrieval implementation, and authorization/execution/final-response work stays in its existing service boundaries.
- Conversation run responses, persisted events, and SSE payloads now expose compact localized ko/en `agent_trace` steps while preserving existing citation/evidence UI fields and avoiding raw prompt/snippet/provider-error leakage.
- Local verification: `uv run ruff check my_agents/api/conversations/agent_trace.py my_agents/api/conversations/run_events.py my_agents/api/conversations/run_lifecycle.py my_agents/api/conversations/serializers.py my_agents/api/conversations/endpoints/stream.py my_agents/conversations/schemas.py my_agents/agents/rag_agent tests/test_rag_agent_contracts.py tests/test_conversations_api.py` passed; `uv run pytest -q tests/test_rag_agent_contracts.py tests/test_context_forge_contracts.py tests/test_conversations_api.py::test_conversation_run_uses_server_owned_history tests/test_conversations_api.py::test_streaming_conversation_run_emits_events_and_persists_result tests/test_conversations_api.py::test_streaming_ambiguous_document_scope_emits_human_clarification_state` passed.
- Hosted smoke was not run for this worker slice; production smoke status remains the separate entry below.

Hosted smoke status on 2026-06-06:

- Production guest smoke passed through the Vercel production BFF and hosted backend after Render
  and Neon were upgraded from the prior constrained tier. Evidence is recorded in
  `docs/product-chat-service/en/16-production-smoke-evidence-2026-06-06.md`.
- Verified health, guest request, operator-issued guest code with Resend HTTP delivery, guest login,
  BFF session/CSRF cookie handoff, `/auth/me` guest restore, guest conversation-limit rejection,
  personal KB creation, text-document creation, ingest, selected-KB streamed run, answer deltas,
  one citation, persisted run detail citations, and run events.
- The guest `/auth/me` response intentionally returns `email=null`; guest identity is explicit via
  `is_guest=true` and `guest_expires_at`.
- Owner manual follow-up confirmed actual Resend guest-code inbox receipt and non-guest signup ->
  email verification -> login smoke.

Agentic RAG workflow v1 verification-lane status on 2026-06-06:

- Redaction requirements and the final evidence checklist are documented in
  `docs/product-chat-service/en/17-agentic-rag-v1-verification-plan.md`.
- `scripts.local_demo_smoke.assert_redacted_run_events` now fails if run event payloads expose
  sensitive keys such as `token`, `password`, `api_key`, `raw_context`, `prompt`, `message`,
  `content`, or `reply`, and the smoke path checks for prompt/account/document strings in run
  events.
- `tests/test_local_demo_smoke.py` covers safe redacted payloads, raw prompt leakage, forbidden
  nested payload keys, and malformed event payloads.
- Full integration evidence is still pending the active backend orchestration and frontend trace
  worker lanes; do not treat this docs/test lane as proof that the integrated agentic RAG v1 story
  is complete.

Earlier hosted smoke status on 2026-06-03:

- Local backend precheck passed with a temporary SQLite database and the backend-only smoke helper:
  `scripts.local_demo_smoke` completed health, auth/session, knowledge-base/document ingestion,
  streamed answer deltas, citations, and events.
- Hosted production had partial positive evidence before infra instability: signup email delivery,
  email verification, login, session restore, knowledge-base creation, document creation, and
  async ingestion enqueue were each observed working through the Vercel production frontend and
  Render backend.
- The full hosted E2E was not completed. The latest rerun reached `GET /api/my-agents/health`
  successfully, created a disposable mailbox, then blocked at hosted signup: one request timed out,
  retry returned Vercel/Render `502`, and a follow-up `GET /api/my-agents/health` also returned
  `502`.
- Current decision: do not add app-level keepalive, warmer, or periodic ping logic. Hosted-demo
  reliability should be solved by deployment tier/configuration when the plan is upgraded, not by
  code that exists only to wake Render or Neon.

## Known gaps / not complete yet

### Product/account lifecycle

- Hosted auth email delivery is implemented through Resend HTTP from the verified `my-agents.dev` sender, with generic SMTP still available as a portable alternate transport. Provider secrets remain environment-owned and are not documented in the repo.
- Auth abuse protection is local/in-process by explicit Phase 1 decision; it is acceptable only for single-process public demos and is not a shared Redis/gateway limiter for multi-worker public deployment.
- No account deletion or profile management surface yet.
- Guest mode is implemented only as an env-gated public-demo path; no durable anonymous daily quota, self-service account deletion, or profile-management surface yet.

### Security and production hardening

- Needs explicit production security review.
- Needs shared/distributed rate limits before multi-worker public deployment; Phase 1 documents and tests the current single-process boundary.
- Credentialed CORS has explicit-origin configuration, but deployed frontend origins still need environment-specific verification.
- Needs secure cookie behavior verified behind the intended deployment/proxy setup.
- Hosted provider/host baseline exists for Render, Vercel, Neon, and Resend HTTP. The remaining smoke gap is the full post-deploy product path: document upload/ingest plus one cited chat answer after each deployment.

### Knowledge ingestion and retrieval

- Text-based upload and extraction supports PDFs through the current local parser chain (`pymupdf_text_v1` primary, then lazy pypdf classification for fallback routing to `pypdf_text_v2`, Docling Markdown, constrained Tesseract OCR, and deterministic stream fallback), Markdown through `utf8_markdown_v1`, plain text through `utf8_text_v1`, and Office uploads through local Markdown parsers (`openpyxl_markdown_v1`, `python_pptx_markdown_v1`, and DOCX-only `docling_docx_markdown_v1`).
- Original uploaded file bytes are not retained yet; only extracted text, source metadata, and supported parse artifacts are stored, so future parser upgrades cannot reprocess old uploads unless users upload the source file again.
- Scanned/image-heavy PDFs have only a constrained local OCR fallback; legacy `.doc`, HTML, CSV/JSON structural parsing, PDF parse-artifact migration, source-file retention, and production layout-aware parsing remain future work.
- Async ingestion progress is available through an additive endpoint (`POST /documents/{id}/ingest/async`) plus direct run polling. Local/default mode still supports in-process threads; hosted mode can set `MY_AGENTS_INGESTION_EXECUTION_MODE=external_worker` and run `python -m my_agents.ingestion_worker`. Frontend multi-file upload fan-out is exposed as a safe `/health` hint backed by `MY_AGENTS_DOCUMENT_UPLOAD_CONCURRENCY` (default `3`). Durable queue semantics and stale-run recovery remain future work.
- Ingestion performance now has a repeatable local benchmark harness plus an opt-in redacted Rich timing panel (`MY_AGENTS_DEBUG_INGESTION_TIMING_LOGGING=true`) for upload parse, PDF parser subphases, extraction/indexing phases, and OpenAI metadata/embedding spans. OpenAI metadata generation overlaps chunk embedding/indexing when metadata mode is OpenAI-backed.
- Embeddings use a provider boundary: deterministic 32-dimensional lexical-hash vectors by default, or opt-in OpenAI embeddings through `langchain-openai` when `MY_AGENTS_EMBEDDING_MODE=openai`; Postgres chunks also store a pgvector `embedding_vector` through Alembic migration `20260521_0007`.
- Retrieval ranking is permission-first RAG Agent -> ContextForge orchestration over pgvector SQL vector search on Postgres, JSON cosine fallback for SQLite/tests, an independent request-local `BM25Okapi` lexical ranking, `chunk_id`-keyed Reciprocal Rank Fusion (`k=60`), entity expansion, structured entity retrieval, deterministic reranking, optional cross-encoder second-stage reranking over bounded authorized candidates, and a narrow personal-document fallback. BM25 reads a lightweight authorized chunk projection, hydrates only lexical top-k models, and retains the `keyword_match` source label, so this default hybrid path needs no dedicated DB index or migration; safe corpus caching/full-text indexing, multilingual tokenization, ANN/vector index tuning, production reranker packaging, and retrieval-quality/latency evals remain future work.
- `rag_agent` is the assistant-facing retrieval-agent boundary. Conversation runs enter it from the `general_assistant` graph; the RAG Agent delegates internally to ContextForge’s thin LangGraph `RetrievalGraph`. The current `rag_agent` contract graph remains the compact trace/grounding verification surface. Hard authorization stays in `RetrievalService`/ContextForge internals.
- Entity extraction is deterministic regex/technical-term extraction, not production NLP/LLM extraction. Canonical entity creation is conflict-safe for concurrent async ingestion: names are pre-collected in stable order and inserted with dialect-aware `ON CONFLICT DO NOTHING` to avoid Postgres unique-index lock cycles.

### Agent/product behavior

- Product conversation runs support SSE progress streaming and incremental `answer_delta` assistant text events. Ordinary OpenAI-backed answers call `ChatOpenAI.stream()`; deterministic fallback and comprehensive-document disclosure paths may still buffer by design.
- Dynamic model-authored reasoning summaries are implemented as a separate trust channel. Luna's
  bounded retrieval-planning explanation and Sol's provider summary are returned through nullable
  `reasoning_summaries`, streamed through `reasoning_summary_delta`, and reconstructed from
  persisted summary events. They remain separate from final answer text, verified trace, and
  evidence. See
  [`28-dynamic-reasoning-summary-contract.md`](./product-chat-service/en/28-dynamic-reasoning-summary-contract.md).
- Temporary conversation files are included in the deployed frontend `9c8e365`; feature enablement
  and end-to-end production verification remain separate. See [the completion record](./completed/document-workspace.md).
- Assistant Mermaid rendering is integrated and shipped in frontend `9c8e365`, with owner-reported
  implementation-time manual testing. Production feature-level and accessibility/stress evidence
  remain limited; see [the completion record](./completed/mermaid-rendering.md).
  AG-UI and A2UI remain future edge adapters and do not replace Product DB domain contracts.
- Known near-future gap: streamed run execution is still coupled to the client HTTP/SSE request. If the client disconnects before `run_completed`, the assistant response may never be persisted; durable server-owned/background run execution is required.
- Completed conversation runs can be refetched with persisted reply, route, and citations.
- `uv run python -m scripts.local_demo_seed` seeds a verified local demo user, text document, and extraction run for file-backed SQLite demos.
- `uv run python -m scripts.local_demo_smoke --base-url http://localhost:8000` verifies the seeded V1 API path without the frontend.
- Credentialed CORS can be enabled for exact frontend origins through `MY_AGENTS_CORS_ALLOWED_ORIGINS`.
- `docs/product-chat-service/en/10-frontend-demo-runbook.md` documents local SQLite demo startup, dev auth outbox, cookie/CSRF expectations, frontend SSE flow, and run-detail refresh.
- `docs/product-chat-service/en/11-v1-phase-0-contract-freeze-evidence-map.md` freezes the Phase 0 strict V1 DoD evidence matrix, backend OpenAPI inventory, frontend gate expectations, and known backend contract gaps by phase.
- `docs/product-chat-service/en/12-public-demo-deployment-readiness.md` defines hosted preview/public smoke gates, provider/dependency decision records, privacy boundaries, rollback paths, and redacted evidence bundle schema.
- Current production graph is still one assistant/controller path, now with a graph-owned RAG Agent retrieval node before memory/answer synthesis.
- Full-document retrieval is an explicit-intent-only first slice. Documents up to the configured 24,000-character threshold can be covered completely; larger documents stop after the first configured 12,000-character range and must disclose partial coverage. Automatic continuation, multi-range summary accumulation, and final whole-document synthesis are not implemented yet.
- The current safety limits are character based, not provider-tokenizer based. Final answer-context token usage, provider-reported usage, and quality/latency comparisons against focused chunk retrieval remain open work.
- The graph version is now `general-assistant-checkpoint-v2`. Waiting runs created under an older graph version cannot be resumed after this deployment; drain or cancel them before rollout, or allow the existing version-mismatch path to fail them safely with `run_graph_version_incompatible`.
- Most route labels are capability metadata and response paths, not separate production specialist agents.
- Bounded tool paths exist for hosted web search, RAG-owned focused/comprehensive selection,
  and the optional hosted document workspace. Arbitrary user-defined tool workflows remain future
  work; authorization and execution limits stay in backend services.
- Memory runtime migration is partially implemented. Recall orchestration runs inside `general_assistant`; PostgreSQL deployments provide PostgresStore semantic candidate search, and every candidate is revalidated through the SQLAlchemy/Product DB governance adapter before entering context. SQLite retains the Product DB relevance fallback. The separate `memory_graph` extraction/update workflow is still unimplemented.
- PostgreSQL deployments now treat run-scoped PostgresSaver and PostgresStore as baseline process-owned infrastructure. The per-user experimental setting controls memory consent and eligibility; HITL availability derives from PostgresSaver presence. Framework setup is an explicit deployment prerequisite, and neither persistence primitive replaces Product DB transcripts or audit records. SQLite keeps non-durable graph execution and Product DB memory recall fallbacks.
- Focused retrieval does not yet let a bounded sufficiency decision request same-document neighboring chunks around authorized anchors. This adaptive expansion is a separate milestone from explicit comprehensive-document review; backend authorization, ordinal/offset windows, round/token budgets, reranking/packing, and citation selectivity must remain deterministic and test-backed even when a model requests more context.

### Deployment/ops

- Basic hosted deployment and CI/CD are configured: Render deploys the backend from Git, Vercel deploys the frontend production branch, Neon provides hosted Postgres, and Resend HTTP sends auth lifecycle email from `my-agents.dev`.
- Hosted signup -> email send -> frontend verification route has been proven after adding frontend `/verify-email` and `/password-reset` landing pages.
- Ingestion now has a web/worker split option: the web process can queue extraction runs only, while a separate ingestion worker claims and processes queued runs.
- Permanent redacted `DEPLOY_DIAG` logs are available for hosted smoke checks and deployment debugging.
- Render's owner-confirmed pre-deploy command runs Alembic automatically. Framework setup,
  reconciliation, isolated regression gates, and authenticated smoke are separate responsibilities;
  the stronger chain remains a documented proposal. See [the Render runbook](./product-chat-service/en/14-render-migration-and-rollback-notes.md#production-pre-deploy-guardrail).
- Opt-in Prometheus text metrics now expose aggregate backend timing for internal
  review. Production dashboards, alerts, OpenTelemetry traces, token/cost metrics,
  and failure-rate metrics remain future work.
- Future observability goal: add Prometheus + Grafana for common backend operations
  metrics, then evaluate Langfuse vs LangSmith for LLM/provider traces, token/cost
  metrics, prompt/version tracking, and eval/retrieval-quality workflows.
- Future retrieval UX goal: define Fast / Balanced / Thorough response-quality profiles
  that tune candidate/vector limits, injected context, reranking/expansion, and optional
  retrieval depth against measured latency while preserving the current high-quality RAG
  behavior as the benchmark.
- Frontend integration lives in the separate frontend repository by design.

## Recent integrated work

The maintenance slices were integrated into develop/main and released through `7a450cc`;
the owner reported no problems during the combined test pass. The later pool hotfix `e62d45a`
and immediate chat recovery are recorded above. Scope and historical test evidence are preserved
in [the maintenance completion record](./completed/backend-maintenance.md).

## Recommended next workflow

### Next: tokenizer-aware retrieval and embedding-index safety

Mermaid integration is complete; do not schedule its implementation again. The previously
following retrieval-safety milestone is the next recommended engineering work, not an automatic
implementation authorization. See [its scope and acceptance criteria](#following-retrieval-move-tokenizer-aware-retrieval-and-embedding-index-safety).
Remaining production feature/idle-path smoke and the proposed pre-deploy/CI gates remain separate
verification and operations work; they were not closed by the frontend release.

### Deferred: attachment-free artifact generation

Do not schedule this as the immediate next task. The current document workspace requires selected
attachments even though its Hosted Shell and artifact pipeline could technically create files from
the user's prompt alone. A future General Assistant-owned typed `create_artifact` capability may
activate an empty hosted container for explicit requests such as creating an HTML or Markdown file,
while reusing the existing artifact, expiry, authorization, usage, and download contracts. The RAG
Agent remains responsible for retrieval, not output generation. Natural-language activation versus
an explicit frontend format control, ambiguity handling, account-credit limits, and required tool
choice remain owner decisions. See the deferred extension in
[`25-openai-document-workspace.md`](./product-chat-service/en/25-openai-document-workspace.md).

### Deferred: continue without document selection

Deferred by the owner's decision on 2026-09-05; reconsider only if explicitly requested.
Missing human input must leave execution paused until answered, cancelled or expired. Silence is
not approval, and mandatory human decisions must not be bypassed through a fallback answer.
The frontend request was clarified with Claude on 2026-09-05: Cancel remains terminal and already
persists cancelled status plus an event; only an assistant message is absent. Frontend `9c8e365`
ships the refresh-safe visible cancellation notice. Do not schedule a new model call
on Cancel or create a redundant stored acknowledgement to replace that notice.

A future explicit “continue without choosing” answer could resume the graph and produce a normal
stored reply, with honest source limitations. Define the semantic answer, eligibility and cost
policy before implementation. Actual cancellations must retain their notice even if continuation
ships. See the [interaction contract](./product-chat-service/en/27-agent-frontend-interaction-contract.md#cancellation-and-declining-a-clarification).

### Deferred: Responses API assistant phase round-tripping

Keep the current answer, reasoning-summary, trace, citation, and SSE user experience unchanged.
Before expanding provider-managed or tool-heavy answer flows, revisit OpenAI Responses API
assistant-message `phase`: user messages must not receive it; `commentary` must not be mistaken for
the completed answer; `final_answer` is the only phase that should enter normal answer streaming and
transcript persistence; and manually replayed assistant history must preserve phase values. The
ordinary provider currently replays plain assistant text without `phase`, does not use
`previous_response_id`, and does not filter output text by phase. This is acknowledged but not
scheduled. Intent, current behavior, adoption triggers, and acceptance requirements are recorded in
[`openai-responses-assistant-phase.md`](./idea/openai-responses-assistant-phase.md).

### Following retrieval move: tokenizer-aware retrieval and embedding-index safety

The next RAG correctness milestone is not to force one tokenizer across every model. It is to keep
each model paired with its own tokenizer while preventing silent reranker truncation and incompatible
embedding-space comparisons. The repository already defaults to `BAAI/bge-reranker-v2-m3`; the
2026-07-14 safe effective-settings audit found an MS MARCO MiniLM runtime override whose English
WordPiece tokenizer reduced a representative 1,500-character Korean query/chunk pair from 2,388
tokens to the model's 512-token input. The same audit found that stored embeddings have no complete
provider/model/index identity and compatibility currently proves only equal vector length.

Suggested order:

1. Use `BAAI/bge-reranker-v2-m3` as the Korean/multilingual evaluation candidate and remove the MS
   MARCO override from the intended runtime.
2. Add Korean, English, and code relevance fixtures plus reranker latency/memory measurements.
3. Add query-aware, model-tokenizer-based document windows and redacted window/truncation evidence;
   authorization must still finish before any reranker input is created.
4. Persist embedding provider/model/dimensions/encoding-or-preprocessing/index-version identity,
   exclude incompatible vectors even when dimensions match, and define a re-embed/backfill path.
5. Add model-aware or provider-reported answer-context token usage while retaining the deterministic
   character budget as a safe fallback.

Stop condition:

- Korean, English, and code fixtures do not silently lose relevant tail evidence during reranking.
- The BAAI reranker candidate has recorded relevance, latency, and memory evidence on the intended
  runtime.
- Query vectors cannot be compared with a different stored embedding index identity.
- Existing chunks and metadata profiles have an explicit compatible/re-embed migration decision.
- Token/usage observability remains redacted, and permission-first retrieval plus offline tests stay
  green.

Analysis and acceptance details:
[`docs/learning/project-notes/tokenizer-consistency-audit-and-rag-index-safety.md`](./learning/project-notes/tokenizer-consistency-audit-and-rag-index-safety.md).

### Parallel product milestone: controlled alpha deploy smoke and tester handoff

The product surface is now strong enough for a small trusted preview. Before sending the link, deploy the latest backend and frontend together, run migrations through the nickname/group-invitation/publish-request heads, refresh hosted OpenAPI evidence, and record a redacted smoke run.

Suggested smoke path:

1. Signup with required nickname -> approval/verification as configured -> login/session restore.
2. Create a group, invite a second user by email, accept the invitation as an existing account or complete invite-token nickname/password signup for a no-account recipient, and confirm the manager-only roster shows nickname but not email.
3. Create or upload a small supported personal source, create a publish request, review readable source preview/content, approve into a group KB, and ask a cited group-knowledge question.
4. Confirm route-addressable frontend group pages work for members, invitations, source spaces, and publish requests; keep per-item publish review in the drawer.
5. Record any issue in `docs/product-chat-service/en/15-deployment-troubleshooting-log.md` and do not broaden the invite until the smoke path is stable.

Stop condition:

- Latest backend tests/lint/format/diff checks pass.
- Latest frontend lint/typecheck/e2e/build passed in the frontend repo before deploy.
- Hosted smoke passes through auth, group invitation, document publish approval, one cited chat answer, and redacted run events.
- Tester invitation copy clearly says “alpha/product preview,” warns against sensitive uploads, and sets expectations about small-file ingestion limits.

### Near implementation milestone: Upstage-backed layout-aware ingestion foundation

The next ingestion-quality milestone is to preserve original uploaded files and generalize the provider-backed parse artifact layer before wiring Upstage Document Parse as an optional cloud parser. DOCX now proves the Markdown-plus-elements artifact shape locally, but source-file retention, parser caching, PDF artifact migration, and provider routing remain future work. The near-term plan lives in `docs/plan/upstage-integration.md`, and the broader architecture idea lives in `docs/idea/layout-aware-ingestion-rag-agent.md`.

Suggested order:

1. Add original source-file retention through a local/dev storage provider plus production-ready storage abstraction.
2. Generalize the existing `document_parse_artifacts` layer for Markdown/HTML/layout metadata across PDFs and future providers, while keeping current `documents.content` compatibility.
3. Introduce a parser provider boundary so current local parsing and future Upstage parsing share one internal contract.
4. Add cost-aware routing and parse caching by source hash + provider/version/mode.
5. Add a re-extract + re-ingest path that can regenerate document text, chunks, embeddings, entities, and metadata from the retained original.

Stop condition:

- Old behavior still supports local/offline parsing and deterministic tests.
- New behavior can retain originals, write/reuse parse artifacts across parser providers, reuse cached parser output, and distinguish re-index from re-extract.
- Upstage can be enabled by config without becoming mandatory for all uploads or tests.

### Next milestone: hosted demo cleanup and smoke verification

The basic hosted path is now real enough to stabilize rather than keep adding features.
Render, Vercel, Neon, Resend HTTP, and `my-agents.dev` are wired for the public demo baseline.

Suggested order:

1. Run and record hosted smoke: signup -> email verification -> login -> small document upload/ingest -> chat with citation.
2. Deploy or verify the ingestion worker path for hosted demo: set `MY_AGENTS_INGESTION_EXECUTION_MODE=external_worker` on the web service and run `uv run python -m my_agents.ingestion_worker` as a separate worker process.
3. Record any new issue in `docs/product-chat-service/en/15-deployment-troubleshooting-log.md`.
4. Keep redacted `DEPLOY_DIAG` logs available; tune only noisy call sites after hosted signup/login/chat smoke remains stable.
5. Treat Render free-tier PDF ingestion slowness as a known resource limitation; prefer small Markdown/plain-text/native-text PDFs for demo until the worker path has smoke evidence or the host is larger.

Stop condition:

- Hosted smoke passes through auth, email verification, login, one document path, and one cited chat answer.
- Deployment diagnostics remain redacted and intentionally available for hosted debugging.
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

## Shipped and completed index

- [x] **Shipped:** Dedicated RAG Agent controller/runtime boundary and bounded retrieval-tool selection. [Completion record](./completed/dedicated-rag-agent.md). Iterative/layout-aware extensions remain future work.
- [x] **Completed:** Atomic admission, shared answer finalization, and document-resolution maintenance integration. [Completion record](./completed/backend-maintenance.md).
- [x] **Shipped:** Mermaid rendering is included in frontend develop/main release `9c8e365`. [Completion record](./completed/mermaid-rendering.md). Feature-level production/accessibility/stress verification remains limited.
- [x] **Shipped:** Temporary conversation files and expanded artifact downloads are included in the released code. [Completion record](./completed/document-workspace.md). Capability enablement and production workflow verification remain separate.

This is concise navigation, not a second history document. New terminal milestones with substantial
scope, decisions, evidence, or limitations link to `docs/completed/<initiative-slug>.md`; small
milestones may remain as one self-contained row. Existing rows are preserved and will be compacted
incrementally when related work next touches them.

| Date | Milestone | Evidence |
| --- | --- | --- |
| 2026-09-02 | Replaced the ordinary final responder's blocking `ChatOpenAI.invoke()` call with real provider chunk streaming, while aggregating the same chunks for final reply/reasoning-summary persistence and preserving normal/resume/replay transport parity. | `my_agents/agents/general_assistant/responders.py`; `my_agents/api/conversations/graph_streaming.py`; `tests/test_responders.py`; `tests/test_graph_streaming.py`; normal/resume/replay SSE tests; bounded live compatibility smoke; bilingual README and streaming docs; learning regression note. |
| 2026-08-31 | Replaced broad default document HITL with authorization-first exact resolution, a bounded ranked shortlist, two private same-run filename refinements, and broad browsing only as the final fallback. Preserved V1 waiting checkpoints and fixed repeated-resume interrupt collection. | Interaction V2 schemas; retrieval resolver; general-assistant graph; conversation run/resume endpoints; `tests/test_interaction_contract.py`; `tests/test_graph.py`; `tests/test_full_document_retrieval.py`; `tests/test_conversations_api.py`; bilingual interaction contract and README pairs. |
| 2026-08-25 | Fixed valid many-small-chunk full-document reads so the 100-citation ceiling no longer erases all provenance. The runtime validates up to 2,000 overlapping chunks and retains 100 evenly distributed citations; the reported 190-chunk Markdown document now completes from first through last chunk. | `my_agents/knowledge/retrieval.py`; `my_agents/knowledge/routing.py`; `tests/test_full_document_retrieval.py`; RAG Agent README/changelog pair; permission-aware RAG docs; learning note 16. |
| 2026-08-24 | Completed the first semantic-intent full-document retrieval vertical slice and added fixed Luna standard/low RAG tool selection: one authorized user-selectable target, complete small-document reads, honest first-range coverage for large documents, typed coverage/event contracts, exact-target replay, checkpoint-safe response composition, and deterministic/provider-failure fallback. Automatic multi-range synthesis, adaptive surrounding-context expansion, and token-aware budgeting remain separate roadmap work. | `my_agents/agents/rag_agent/tool_selection.py`; `my_agents/agents/general_assistant/graph.py`; `my_agents/agents/general_assistant/rag_retrieval.py`; `my_agents/agents/rag_agent/retrieval.py`; `my_agents/knowledge/retrieval.py`; conversation schemas/events/serializers; `tests/test_rag_agent_tool_selection.py`; `tests/test_full_document_retrieval.py`; settings/env/docs. |
| 2026-08-09 | Added an unauthenticated runtime guest-policy contract, removed stale numeric limits from guest email copy, and raised repo defaults to 3 conversations, 20 prompts, and 5 document uploads. Provider delivery passed with a Resend test recipient, while hosted automatic approval remained inactive pending its deployment flag. | `my_agents/api/auth.py`; `my_agents/auth/schemas.py`; `my_agents/settings.py`; auth email templates; `tests/test_guest_access_api.py`; `tests/test_auth_email.py`; README pair; auth/deployment docs. |
| 2026-08-09 | Added stable additive API error codes, froze the persisted agent-event vocabulary and display-safe payload/trace schemas, and proved external-worker ingestion progress is visible through independent polling sessions. | `my_agents/api/errors.py`; `my_agents/conversations/schemas.py`; `my_agents/api/conversations/run_events.py`; `tests/test_api_error_contract.py`; `tests/test_agent_event_contract.py`; `tests/test_knowledge_ingestion.py`; README pair; observability/ingestion docs. |
| 2026-07-24 | Made permission-filtered hybrid retrieval the ContextForge default with independent vector and request-local BM25Okapi rankings fused by RRF over stable `chunk_id`, without a database migration. | `pyproject.toml`; `uv.lock`; `my_agents/knowledge/retrieval.py`; `my_agents/agents/context_forge/candidates.py`; `my_agents/agents/context_forge/fusion.py`; `tests/test_context_forge_reranking.py`; `tests/test_permission_aware_rag.py`; README and ContextForge README pairs. |
| 2026-06-25 | Added generic/repo-local performance workflow support, ingestion benchmark tooling, redacted ingestion timing panels, OpenAI metadata/embedding overlap, and lazy PDF classification; local Aliro PDF profile improved from 36.16s to 16.57s end-to-end while preserving parser/source/chunk/entity/relationship counts. | `.codex/skills/performance-optimizer/`; `.codex/skills/rag-performance-optimizer/SKILL.md`; `scripts/measure_ingestion_performance.py`; `my_agents/knowledge/timing.py`; `my_agents/knowledge/uploads.py`; `my_agents/knowledge/pdf_uploads.py`; `my_agents/knowledge/extraction.py`; `tests/test_knowledge_ingestion.py`; `tests/test_settings.py`; README pair; ingestion docs; performance logs; full suite `459 passed, 1 skipped`. |
| 2026-06-23 | Added DOCX-only upload, Markdown parse artifacts, ingestion/citation coverage, and legacy `.doc` rejection. | `my_agents/knowledge/office_uploads.py`; upload route descriptions; `tests/test_office_uploads.py`; `tests/test_knowledge_ingestion.py`; `tests/test_publish_requests.py`; ingestion docs. |
| 2026-06-22 | Added a graph-level source-selection gate so explicit KB bypass and common/web requests can skip ContextForge, removed language-specific general-assistant web-search hints, and delayed optional cross-encoder model loading until the first non-empty ContextForge rerank call. | `my_agents/agents/general_assistant/retrieval_gate.py`; `my_agents/agents/general_assistant/graph.py`; `my_agents/agents/general_assistant/rag_retrieval.py`; `my_agents/agents/general_assistant/responders.py`; `my_agents/agents/capabilities.py`; `my_agents/agents/context_forge/reranking.py`; `tests/test_retrieval_gate.py`; `tests/test_graph.py`; `tests/test_responders.py`; `tests/test_context_forge_reranking.py`; README and agent README pairs. |
| 2026-06-16 | Documented the General Assistant -> RAG Agent -> ContextForge architecture correction with a dedicated change report and review map. | `docs/product-chat-service/en/22-general-assistant-rag-agent-architecture-change-report.md`; product docs index; implementation tracking docs section. |
| 2026-06-16 | Added opt-in Prometheus timing metrics for internal performance and quality analysis without changing the frontend/product surface. | `pyproject.toml`; `my_agents/observability/metrics.py`; `my_agents/api/metrics.py`; `my_agents/api/__init__.py`; ContextForge/retrieval/embedding/graph/run timing hooks; `tests/test_metrics.py`; README pair; observability docs; `ROADMAP.md`. |
| 2026-06-14 | Product status review refreshed roadmap/tracking and marked the current version as controlled-alpha worthy after deploy smoke. | `docs/implementation-tracking.md`; `ROADMAP.md`; local docs consistency review; backend verification recorded above. |
| 2026-06-14 | Publish-request review became owner-actionable: backend responses expose source labels, filenames, excerpts, and source-document content lookup for confident approve/reject; frontend renders list-scale group management as dedicated routes while keeping per-request review in a drawer. | Backend commit `3812ef3`; frontend commits `5eefc77`, `58212af`, `19f33f0`; `tests/test_publish_requests.py`; `tests/test_kb_openapi_contract.py`; frontend `e2e/group-knowledge-v1.spec.ts`. |
| 2026-06-14 | Fixed no-account group invitations so token-proved invitees choose nickname/password only, keep email as sign-in identity, and accept membership in one flow. | `my_agents/groups/service.py`; `my_agents/api/groups.py`; `my_agents/auth/email_templates/`; `tests/test_group_invitations_api.py`; `tests/test_auth_email.py`; README pair; group/nickname contract docs. |
| 2026-06-14 | Implemented and documented the nickname signup and manager-only member roster contract. | `docs/product-chat-service/en/20-nickname-signup-member-roster-contract.md`; `docs/product-chat-service/ko/20-nickname-signup-member-roster-contract.md`; README pair; group-permission docs; implementation tracking. |
| 2026-06-10 | Added a thin ContextForge LangGraph `RetrievalGraph` wrapper as the conversation-run retrieval entrypoint and future agent tool/subgraph seam. | `my_agents/agents/context_forge/graph.py`; `my_agents/agents/context_forge/__init__.py`; `my_agents/api/conversations/retrieval_context.py`; `tests/test_context_forge_contracts.py`; ContextForge README pair; retrieval architecture docs; targeted ContextForge/RAG tests. |
| 2026-06-07 | Added real streamed assistant-message replay and newest-first conversation list ordering for the chat sidebar. | `my_agents/api/conversations/endpoints/replay.py`; `my_agents/api/conversations/endpoints/conversations.py`; `tests/test_conversations_api.py`; `tests/test_kb_openapi_contract.py`; streaming frontend contract docs; `uv run ruff check . --no-cache`; `uv run ruff format --check .`; `uv run pytest -q` (306 passed, 2 skipped). |
| 2026-06-06 | Added RAG Agent contracts for the agentic RAG workflow and compact localized trace payloads for run responses/SSE/events. | `my_agents/agents/rag_agent/`; `my_agents/api/conversations/agent_trace.py`; `my_agents/api/conversations/run_events.py`; `my_agents/api/conversations/run_lifecycle.py`; `my_agents/api/conversations/serializers.py`; `my_agents/api/conversations/endpoints/stream.py`; `my_agents/conversations/schemas.py`; `tests/test_rag_agent_contracts.py`; `tests/test_conversations_api.py`; RAG Agent README pair; local targeted Ruff/pytest evidence. |
| 2026-05-27 | Added external-worker ingestion mode so hosted async document ingestion no longer needs to run inside the web request process. | `my_agents/knowledge/ingestion_worker.py`; `my_agents/ingestion_worker.py`; `my_agents/api/documents.py`; `my_agents/settings.py`; `.env.example`; `tests/test_knowledge_ingestion.py`; `tests/test_settings.py`; README pair; Render migration/troubleshooting docs. |
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

1. Read the task-relevant sections of this file, then use `docs/README.md` to route deeper reading.
2. Check `git status --short`.
3. Run or inspect the latest relevant tests.
4. Update this file if your workflow changes completion, priorities, or known gaps.
5. Compact newly terminal work according to `docs/README.md`; do not preload completion records
   unless the task needs history or regression context.
6. Keep `.omx/` notes as local-only; do not rely on them for cross-machine continuity.
### 2026-06-07 — Group upload hidden staging boundary

- Added `KnowledgeBasePurpose` and a `team_upload_staging` purpose for private upload buffers.
- Added `POST /knowledge-bases/team-upload-staging` to create/reuse a hidden personal staging KB for group document publication.
- Excluded staging KBs from normal KB lists, chat selected/all source resolution, retrieval filters, and whole-KB publish requests.
- Preserved document-copy publication: staged documents can be copied into a target group KB, and only the approved group copy is ingested/retrieved.
- Captured the service flow in `docs/product-chat-service/en/18-team-upload-staging-flow.md` and the Korean companion note.

Verification so far: targeted pytest for staging publish/retrieval, OpenAPI contract, and Alembic head migration.
