# my-agents

[English](./README.en.md) | 한국어

`my-agents`는 실용적인 AI 채팅 제품 표면을 만들기 위한 backend-only FastAPI + LangGraph 서비스입니다. 프론트엔드는 별도 저장소로 분리하고, 이 저장소는 API contract, auth/session, 문서 지식 workflow, conversation run, retrieval, citation, agent activity event에 집중합니다.

현재 프로젝트 상태는 [`docs/implementation-tracking.md`](./docs/implementation-tracking.md)에서 먼저 확인하세요. 상세 backlog는 [`ROADMAP.md`](./ROADMAP.md)에 있습니다.

## 구현된 내용

현재 backend는 얇지만 동작하는 제품 slice입니다.

- health, auth, groups, documents, knowledge bases, conversations, runs, streaming, events route를 제공하는 FastAPI app.
- deterministic route classification과 기본 OpenAI-backed response generation을 사용하는 LangGraph general assistant path.
- credential 없이 test/smoke check를 실행할 수 있는 deterministic offline/test mode.
- email/password auth, app-owned session, CSRF-aware logout, dev outbox, signup/guest approval gates.
- group, document, knowledge-base, permission 기반.
- PDF, Markdown, plain text, `.xlsx`, `.pptx`를 지원하는 KB-scoped document upload/create, team-upload staging, ingestion, extraction-run progress, chunk, entity, metadata profile, embedding, pgvector-ready retrieval.
- permission-aware RAG, structured entity retrieval, reranking seam, packed context, citation, redacted retrieval evidence를 담당하는 ContextForge retrieval service.
- server-owned conversation, run history, SSE assistant text streaming, run replay/cancel, persisted citation, frontend-safe activity event와 compact ko/en agent trace.
- review/list/delete API, relevance-minimized recall, deterministic write-policy gate, suggest-confirm lifecycle, document-derived provenance/staleness, conflict-aware provider context를 가진 사용자별 opt-in long-term memory.

자세한 설명은 README 대신 docs에 둡니다.

| 영역 | 상세 문서 |
| --- | --- |
| Service/API split | [`docs/product-chat-service/ko/01-service-foundation-scaffold.md`](./docs/product-chat-service/ko/01-service-foundation-scaffold.md) |
| Auth/session | [`docs/product-chat-service/ko/02-first-party-auth-sessions.md`](./docs/product-chat-service/ko/02-first-party-auth-sessions.md) |
| Group/permission | [`docs/product-chat-service/ko/03-group-document-permissions.md`](./docs/product-chat-service/ko/03-group-document-permissions.md) |
| Conversation/run | [`docs/product-chat-service/ko/04-server-owned-conversations.md`](./docs/product-chat-service/ko/04-server-owned-conversations.md) |
| Knowledge ingestion | [`docs/product-chat-service/ko/05-knowledge-ingestion-extraction.md`](./docs/product-chat-service/ko/05-knowledge-ingestion-extraction.md) |
| RAG/citation | [`docs/product-chat-service/ko/06-permission-aware-rag.md`](./docs/product-chat-service/ko/06-permission-aware-rag.md) |
| Observability/eval | [`docs/product-chat-service/ko/07-agent-observability-evals.md`](./docs/product-chat-service/ko/07-agent-observability-evals.md) |
| Postgres/Alembic/pgvector | [`docs/product-chat-service/ko/08-postgres-alembic-neon.md`](./docs/product-chat-service/ko/08-postgres-alembic-neon.md) |
| Streaming frontend contract | [`docs/product-chat-service/ko/09-http-streaming-frontend-contract.md`](./docs/product-chat-service/ko/09-http-streaming-frontend-contract.md) |
| Local frontend demo runbook | [`docs/product-chat-service/ko/10-frontend-demo-runbook.md`](./docs/product-chat-service/ko/10-frontend-demo-runbook.md) |
| V1 contract evidence map | [`docs/product-chat-service/ko/11-v1-phase-0-contract-freeze-evidence-map.md`](./docs/product-chat-service/ko/11-v1-phase-0-contract-freeze-evidence-map.md) |
| KB-first OpenAPI handoff | [`docs/product-chat-service/ko/12-knowledge-base-path-openapi-handoff.md`](./docs/product-chat-service/ko/12-knowledge-base-path-openapi-handoff.md) |
| Public demo readiness | [`docs/product-chat-service/ko/12-public-demo-deployment-readiness.md`](./docs/product-chat-service/ko/12-public-demo-deployment-readiness.md) |
| Hybrid retrieval reference | [`docs/product-chat-service/ko/12-retrieval-agent-hybrid-reference.md`](./docs/product-chat-service/ko/12-retrieval-agent-hybrid-reference.md) |
| Container deployment path | [`docs/product-chat-service/ko/13-generic-container-deployment-path.md`](./docs/product-chat-service/ko/13-generic-container-deployment-path.md) |
| Team upload staging / 팀 업로드 임시 저장 | [`docs/product-chat-service/ko/18-team-upload-staging-flow.md`](./docs/product-chat-service/ko/18-team-upload-staging-flow.md) |
| LangGraph-native memory migration | [`docs/product-chat-service/ko/19-langgraph-native-memory-migration.md`](./docs/product-chat-service/ko/19-langgraph-native-memory-migration.md) |
| Script commands / 스크립트 명령 | [`scripts/README.md`](./scripts/README.md) |
| Layout-aware RAG idea | [`docs/idea/layout-aware-ingestion-rag-agent.md`](./docs/idea/layout-aware-ingestion-rag-agent.md) |

## 경계

- 이 저장소는 backend-only입니다. 프론트엔드 작업은 `~/Git/my-agents-frontend` 같은 별도 저장소에서 다룹니다.
- Production-surface 동작의 LLM provider는 OpenAI를 기준으로 합니다. Test는 기본적으로 offline이어야 합니다.
- Route label은 deterministic classification과 capability metadata를 설명합니다. 아직 별도 specialized agent가 실행된다는 뜻은 아닙니다.
- 학습용 simulated-agent graph experiment는 `~/Git/Playground/langgraph-playground`의 standalone `simulated_agents/` 패키지로 이동했습니다. 이 backend repo는 production API surface에 집중합니다.
- 실제 secret을 commit하지 마세요. Local `.env` 파일은 git에서 무시하며, `.env.example`은 안전한 placeholder 문서입니다.

## 아키텍처 요약

```mermaid
flowchart TD
    Frontend["Separate frontend or API client"] --> API["FastAPI app"]
    API --> Auth["Auth/session/CSRF"]
    API --> KB["Knowledge bases + documents"]
    API --> Runs["Conversation runs / SSE"]
    KB --> Ingest["Ingestion + chunks + entities + embeddings"]
    Runs --> ContextForge["ContextForge permission-aware retrieval"]
    Runs --> Memory["Opt-in user memory service"]
    ContextForge --> RAGAgent["RAG Agent contract graph"]
    ContextForge --> GraphInput["Authorized retrieved context"]
    GraphInput --> Graph["General assistant LangGraph"]
    Memory --> GraphInput
    Graph --> Provider["OpenAI or deterministic provider"]
    RAGAgent --> Events["Verified agent trace + grounding checks"]
    Graph --> Events
    Auth --> DB[("SQLAlchemy DB")]
    KB --> DB
    Ingest --> DB
    Runs --> DB
    Memory --> DB
    Events --> DB
```

팀 업로드는 RAG retrieval에서 제외되는 숨겨진 개인 임시 KB를 사용하고, 승인 후 소스를 팀 KB로 복사한 뒤에만 검색됩니다. Service layer가 auth, permission, source policy, persistence, retrieval boundary, citation, event를 소유합니다. ContextForge는 여전히 general assistant에 전달할 authorized context를 검색하고, `rag_agent`는 그 경로를 감싸는 graph-shaped RAG Agent contract로 trace stage와 grounding check를 제공합니다. Chat run은 권한이 있는 개인·공유·팀 KB를 하나의 `knowledge_base_selection` contract로 선택하며, conversation transcript는 사용자 소유 비공개 범위로 유지됩니다.

## Long-term memory

Long-term memory는 기본적으로 꺼져 있으며 authenticated user 단위로 격리됩니다.
Product DB는 계속 visible conversation, final assistant answer, citation, billing/audit,
run event, redacted memory-source snapshot의 source of truth입니다. Memory는 사용자가 opt in한 뒤에만 provider context에
들어갈 수 있는 별도 source channel입니다.

Architecture direction: 현재 SQLAlchemy 기반 memory 구현은 안전한 V1 governance/runtime scaffold이며
최종 LangGraph-native memory runtime은 아닙니다. 장기 목표는 Product DB가 consent, provenance,
audit, source invalidation, user management를 계속 소유하고, active memory storage/search와
extraction은 LangGraph Store와 별도 `memory_graph`로 이동하는 것입니다. 자세한 내용은
[LangGraph-native memory migration note](./docs/product-chat-service/ko/19-langgraph-native-memory-migration.md)를 봅니다.

구현된 memory route는 다음과 같습니다.

- `GET /memories/settings`, `PATCH /memories/settings`: opt-in 상태 확인/변경
- `GET /memories`, `POST /memories`, `POST /memories/{id}/deactivate`,
  `DELETE /memories/{id}`: 명시적 user memory 관리. Public create request는 client가 주장하는 provenance ID, arbitrary value payload, suggestion TTL을 받지 않습니다
- `GET /memories/suggestions`, `POST /memories/suggestions`,
  `POST /memories/suggestions/{id}/confirm`,
  `POST /memories/suggestions/{id}/reject`: suggest-confirm write lifecycle

Memory가 disabled이면 memory retrieval을 주입하지 않고 write도 받지 않습니다. 다만 기존
record는 사용자가 review/delete할 수 있게 남겨둡니다. Delete는 저장된 content/value를 scrub하고 최소 tombstone만 남깁니다. Rejected/expired/confirmed suggestion도 제안된 memory text를 scrub해서 declined/decided suggestion에 duplicate memory content가 남지 않게 합니다. Auto-store와
suggest-confirm path는 deterministic category/sensitivity guard를 거치며, sensitive fact는 저장하지
않습니다. Stable preference는 durable preference처럼 보이는 내용만 global recall 대상이 될 수 있습니다. Document-derived memory는 document provenance가 필요하고, source document가 삭제되면
같은 transaction에서 stale로 표시되어 provider context에서 제외됩니다. General assistant는 `SourceContextBundle`을 통해
recent Product DB conversation, authorized document context, relevance-minimized stored memory, material source conflict를
분리해서 받습니다. 검증된 stable preference는 global하게 recall될 수 있지만, project/personal/document-derived memory는 query relevance가 필요합니다. 최신 conversation과 stored memory가 충돌하면 provider prompt는 최신
conversation을 우선하고 conflict를 설명하도록 지시합니다. Replay/regeneration은 historical memory content를 재사용하지 않고 현재 memory opt-in 상태와 현재 active memory를 사용합니다. Completed run에는 audit용 redacted memory ID/category/provenance/conflict count만 저장합니다.

## 문서 업로드 지원

Knowledge-base document upload는 현재 다음 형식을 받습니다.

- text-based PDF (`.pdf`)
- Markdown (`.md`, `.markdown`)
- UTF-8 plain text (`.txt`)
- modern Excel workbook (`.xlsx`)
- modern PowerPoint deck (`.pptx`)

Office upload는 `openpyxl`과 `python-pptx`로 local parsing하여 canonical Markdown으로
변환합니다. Backend는 원본 Office bytes나 object-storage key를 knowledge base에 보관하지 않고,
derived parse artifact만 저장합니다. 저장되는 artifact는 Markdown, parser metadata, warning,
worksheet cell range 또는 slide/shape number 같은 source-location element입니다. Office에서 생성된
chunk citation은 `source_location_json`을 노출하고, PDF citation은 기존처럼 `source_page`를 유지합니다.

현재 제한: modern OOXML `.xlsx`/`.pptx`만 지원합니다. Legacy `.xls`/`.ppt`/`.doc` 형식과 Word
upload는 거절됩니다. Upload는 기존 V1 문서 제한인 5 MiB를 공유하며, 안전한 OOXML archive/parser
budget 안에 들어와야 합니다. Office parsing은 보이는 workbook cell, slide text, slide table을
추출합니다. OCR이나 pixel-perfect layout 보존은 아직 하지 않습니다.

## 설정

의존성 설치:

```bash
uv sync
```

Local 설정 파일 생성:

```bash
cp .env.example .env
```

기본 local chat은 OpenAI-backed reply를 사용합니다. OpenAI mode를 쓰려면 실제 key를 설정하세요.

```bash
MY_AGENTS_RESPONSE_MODE=openai
OPENAI_API_KEY=sk-your-project-key
MY_AGENTS_OPENAI_MODEL=gpt-5.5
```

Credential 없는 test와 local smoke check에는 deterministic mode를 사용합니다.

```bash
MY_AGENTS_RESPONSE_MODE=deterministic
```

전체 설정 목록은 [`.env.example`](./.env.example)을 확인하세요. CORS, cookie, CSRF, dev outbox, seeded local data, SSE/run-detail expectation은 [frontend demo runbook](./docs/product-chat-service/ko/10-frontend-demo-runbook.md)에 정리되어 있습니다.
중단된 대화 실행이 frontend에 계속 “작성 중”으로 남지 않도록 `MY_AGENTS_ACTIVE_RUN_STALE_AFTER_SECONDS` 기본값은 120초이며, hosted demo에서는 이 값을 짧게 유지하세요.

Signup과 guest code는 별도 auto-approval gate를 가집니다. 두 gate의 기본값은
`false`라서 public 배포에서 임의의 이메일 사용자가 바로 LLM route를 쓰지 못합니다.
`MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL=false`이면 signup은 pending user만 만들고,
operator가 approve하면 verification token/link를 출력합니다. `--send-email`을
붙이면 같은 verification email도 보냅니다.

Guest access를 켜면 public client는 `POST /auth/guest/request`에 email을 보내고
`status=accepted`만 받습니다. `MY_AGENTS_GUEST_CODE_AUTO_APPROVAL=false`이면
operator가 one-time code를 발급합니다. `true`이면 backend가 code를 만들고 이메일로
자동 발송하며, 발송 실패 시 usable code를 남기지 않습니다. 이메일 언어는 기본 한국어이며
`--lang en`으로 영어를 선택할 수 있습니다.

```bash
uv run python -m scripts.ops account approve \
  --email user@example.com \
  --send-email

# 같은 코드를 출력하고, 추가로 기본 한국어 이메일을 전송합니다.
MY_AGENTS_GUEST_ACCESS_ENABLED=true uv run python -m scripts.ops guest issue \
  --email guest@example.com \
  --send-email
```

## 로컬 실행

API 시작:

```bash
uv run fastapi dev main.py
```

Fallback:

```bash
uv run uvicorn main:app --reload
```

실행 중인 서버의 OpenAPI 문서:

```text
http://127.0.0.1:8000/openapi.json
```

FastAPI 서버 없이 CLI chat loop 실행:

```bash
uv run python -m my_agents.cli
```

Hosted/demo 배포에서는 async document ingestion을 web process 밖에서 실행하세요.

```bash
MY_AGENTS_INGESTION_EXECUTION_MODE=external_worker uv run uvicorn main:app --host 0.0.0.0 --port 8000
uv run python -m my_agents.ingestion_worker
```

Backend restart 등으로 남은 active conversation run은 polling 또는 다음 prompt 전 cleanup에서 실패/취소
상태로 정리됩니다. Hosted/demo UX에서는 기본 120초를 사용하며 필요하면
`MY_AGENTS_ACTIVE_RUN_STALE_AFTER_SECONDS`로 조정하세요.

## 주요 검사

```bash
uv run pytest -q
uv run ruff check . --no-cache
uv run ruff format --check .
git diff --check
```

선택 사항: local Postgres/pgvector helper.

```bash
uv run python -m scripts.dev_pgvector up --migrate
set -a; source .env.pgvector.local; set +a
MY_AGENTS_RESPONSE_MODE=deterministic uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

Database 상세 설명: [`docs/product-chat-service/ko/08-postgres-alembic-neon.md`](./docs/product-chat-service/ko/08-postgres-alembic-neon.md).

## 빠른 API smoke

Health:

```bash
curl http://127.0.0.1:8000/health
```

Legacy/dev assistant smoke endpoint:

```bash
curl -X POST http://127.0.0.1:8000/assistant/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Help me study LangGraph routing","history":[]}'
```

제품 client는 `/assistant/chat`보다 conversation-run endpoint를 우선 사용해야 합니다. 전체 local product flow는 다음 helper를 사용하세요.

```bash
uv run python -m scripts.local_demo_seed
uv run python -m scripts.local_demo_smoke --base-url http://127.0.0.1:8000
```

자세한 내용: [`docs/product-chat-service/ko/10-frontend-demo-runbook.md`](./docs/product-chat-service/ko/10-frontend-demo-runbook.md).

## 문서 지도

- Product docs: [`docs/product-chat-service/ko/README.md`](./docs/product-chat-service/ko/README.md)
- Ideas: [`docs/idea/`](./docs/idea/)
- Learning notes: [`docs/learning/README.md`](./docs/learning/README.md)
- Script commands / 스크립트 명령: [`scripts/README.md`](./scripts/README.md)
- General assistant implementation: [`my_agents/agents/general_assistant/README.md`](./my_agents/agents/general_assistant/README.md)
- ContextForge retrieval boundary: [`my_agents/agents/context_forge/README.md`](./my_agents/agents/context_forge/README.md)
- RAG Agent workflow contract: [`my_agents/agents/rag_agent/README.md`](./my_agents/agents/rag_agent/README.md)

## 향후 방향

Near-term work는 [`docs/implementation-tracking.md`](./docs/implementation-tracking.md)와 [`ROADMAP.md`](./ROADMAP.md)에 기록합니다. 중요한 future track은 production parser provider, layout-aware ingestion artifact, 현재 contract graph를 넘어서는 richer tool-using RAG Agent graph, retrieval eval 강화, deployment hardening, scoped instruction profile입니다.
