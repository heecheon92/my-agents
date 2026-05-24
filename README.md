# my-agents

[English](./README.en.md) | 한국어

학습과 커리어 지원을 위한 백엔드 전용 FastAPI + LangGraph 어시스턴트/라우터 기반 프로젝트입니다.

## 목적과 v0 범위

`my-agents` v0는 OpenAI 기반 응답 생성을 기본으로 사용하는 포트폴리오용 AI 채팅 서비스 백엔드입니다. 현재 버전은 다음을 보여줍니다.

- 헬스 체크, auth, group, document, knowledge-base, conversation run을 위한 FastAPI API 표면
- 명시적인 상태, 노드, `START`, `END`, 조건부 라우팅을 사용하는 실제 LangGraph `StateGraph`
- 향후 어시스턴트 범주를 위한 결정론적 라우트 라벨 분류
- first-party session, group/document permission, server-owned conversation history
- 텍스트 문서 ingestion, permission-aware retrieval, citation, redacted agent event
- 타입이 지정된 요청/응답 계약과 자동화된 테스트 대상

v0는 의도적으로 thin end-to-end slice로 유지합니다. 이 저장소는 백엔드 전용 학습/포트폴리오 산출물이며, 프론트엔드 애플리케이션은 별도 저장소에서 다룹니다.

## 이식 가능한 구현 추적

현재 완성도, 완료된 milestone, 다음 workflow 우선순위, 다른 머신의 agent handoff는 [`docs/implementation-tracking.md`](./docs/implementation-tracking.md)를 repo-tracked source of truth로 사용합니다. 더 자세한 v1 backlog/checklist는 [`ROADMAP.md`](./ROADMAP.md)를 companion 문서로 사용합니다. 로컬 `.omx/` 상태는 runtime context로는 유용하지만 머신 간에 공유되지 않습니다.

## 명시적인 v0 안내

v0의 기본 응답 모드는 OpenAI 기반입니다. 채팅 응답 생성을 실행하려면 `OPENAI_API_KEY`가 필요합니다. 테스트와 오프라인 확인을 위해 `MY_AGENTS_RESPONSE_MODE=deterministic` 모드는 계속 유지합니다.

분류는 계속 결정론적으로 수행되며, 선택한 OpenAI GPT 모델은 최종 응답 텍스트만 생성합니다. API 키 없이 실행해야 하는 경우에는 deterministic 모드로 전환합니다. v0의 라우트 라벨과 capability metadata는 현재 동작을 정직하게 설명하는 정보일 뿐이며, 별도 전문 에이전트가 실행되었다는 뜻은 아닙니다. 현재 product RAG/permission/event 기능은 API/service layer가 LangGraph 실행 주변에서 제공하는 기능입니다. 학습 전용 simulation은 `my_agents/simulated_agents/` 아래에 두며 production API/CLI surface로 import하지 않습니다.

## 프론트엔드 없음

이 저장소에는 프론트엔드 UI가 없습니다. v0 인터페이스는 FastAPI 백엔드 API와 로컬 명령줄 테스트/실행 워크플로입니다. 프론트엔드는 `~/Git/Portfolio/my-agents-frontend` 같은 별도 저장소에서 연결하는 것을 전제로 합니다.

## 아키텍처 개요

```mermaid
flowchart TD
    Client["HTTP client"]
    CLI["Terminal CLI"]
    API["FastAPI app"]
    Graph["General assistant graph"]
    Classifier["classify_request"]
    Capability["capability registry"]
    Router{"route label"}
    General["respond_general"]
    Learning["respond_learning"]
    Research["respond_research"]
    Project["respond_project"]
    Career["respond_career"]
    Provider{"response provider"}
    OpenAI["ChatOpenAI default"]
    Deterministic["deterministic offline/test"]
    Response["typed response"]

    Client --> API
    API --> Graph
    CLI --> Graph
    Graph --> Classifier
    Classifier --> Capability
    Classifier --> Router
    Router --> General
    Router --> Learning
    Router --> Research
    Router --> Project
    Router --> Career
    General --> Provider
    Learning --> Provider
    Research --> Provider
    Project --> Provider
    Career --> Provider
    Provider --> OpenAI
    Provider --> Deterministic
    OpenAI --> Response
    Deterministic --> Response
```

현재 그래프는 `my_agents/agents/general_assistant/` 아래에 있으며 하나의 어시스턴트/라우터 흐름을 가집니다. 분류는 결정론적으로 수행됩니다. 응답 노드는 provider 인터페이스를 통해 라우트별 응답을 구성합니다. 기본값은 `langchain-openai`의 `ChatOpenAI`이며, 오프라인/테스트용으로 결정론적 템플릿 모드를 사용할 수 있습니다. 라우트별 응답 노드는 별도의 에이전트가 아닙니다. Production surface agent 코드는 `my_agents/agents/` 아래에 두고, 학습 전용 simulation은 `my_agents/simulated_agents/` 아래에 분리합니다.


## 제품 서비스 표면

```mermaid
flowchart TD
    Frontend["Separate frontend"] --> Auth["/auth session"]
    Frontend --> Groups["/groups memberships"]
    Frontend --> Docs["/documents + permissions"]
    Frontend --> KB["/knowledge-bases + ingest"]
    Frontend --> Runs["/conversations/{id}/runs or /runs/stream"]
    Runs --> History["server-owned messages"]
    Runs --> Retrieval["permission-aware retrieval"]
    Retrieval --> GraphExpand["entity mention expansion"]
    Runs --> Assistant["general assistant LangGraph"]
    Runs --> Citations["citations"]
    Runs --> Events["redacted agent events"]
    Auth --> DB[("SQLAlchemy DB")]
    Groups --> DB
    Docs --> DB
    KB --> DB
    History --> DB
    Citations --> DB
    Events --> DB
```

현재 핵심 설계 선택은 “AI graph만 있는 백엔드”가 아니라, auth/permission/conversation/knowledge lifecycle은 서비스 레이어가 소유하고 LangGraph는 그 안에서 응답 생성 경로로 호출되는 구조입니다. 이 덕분에 프론트엔드가 보아야 하는 product surface와 향후 더 복잡한 agent orchestration 확장 지점이 분리됩니다.

## 그래프 흐름

1. API가 비어 있지 않은 `message`와 선택적 `history`를 포함한 채팅 요청을 받습니다.
2. FastAPI/Pydantic이 그래프 실행 전에 요청을 검증합니다.
3. API가 공개 JSON의 `message`/`history`를 LangChain messages로 변환하고, LangGraph 상태는 `messages`, 라우트 결정, 응답, 그래프 메타데이터를 저장합니다.
4. `classify_request`가 결정론적 규칙을 적용해 라우트 라벨과 설명을 반환합니다.
5. 그래프가 선택된 라우트의 `AgentCapability` metadata를 붙입니다.
6. 조건부 그래프 엣지가 라우트 라벨에 맞는 응답 구성 노드를 선택합니다.
7. 선택된 response provider가 capability guidance와 함께 응답을 구성합니다.
8. 그래프가 `reply`, `route.label`, `route.explanation`, `handled_by`를 포함한 타입이 지정된 응답을 반환합니다.

`handled_by`는 단일 그래프 경로(`personal_assistant_graph`)를 식별합니다. 전문 에이전트를 식별하는 값이 아닙니다.

## 에이전트 구현 패턴

Production surface 에이전트는 `my_agents/agents/<agent_name>/` 아래에 독립된 폴더로 추가합니다. 학습 전용 architecture experiment는 `my_agents/simulated_agents/<agent_name>/` 아래에 둡니다. 현재 production 예시는 [`my_agents/agents/general_assistant/`](./my_agents/agents/general_assistant/README.md)입니다.

권장 책임 분리는 다음과 같습니다.

```mermaid
flowchart TD
    API["API 또는 CLI"] --> Graph["graph.py"]
    Graph --> Classifier["classifier.py"]
    Graph --> Responder["responders.py"]
    Responder --> Provider{"response provider"}
    Provider --> HostedTools["OpenAI hosted tools"]
    Provider --> CustomTools["custom tools.py"]

    Graph -. "workflow, state, routing" .-> GraphNote["흐름 제어"]
    Responder -. "prompt, model call, tool binding" .-> ResponderNote["모델 동작"]
    CustomTools -. "local Python tool implementation" .-> ToolNote["도구 구현"]
```

원칙:

- `graph.py`는 workflow, state, node routing을 담당합니다.
- `classifier.py`는 사용자 입력을 route label로 분류합니다.
- `my_agents/agents/`는 production surface capability를 위한 위치입니다.
- `my_agents/simulated_agents/`는 학습/테스트용 architecture experiment를 위한 위치입니다.
- `responders.py`는 prompt 구성, `ChatOpenAI` 호출, LLM tool binding을 담당합니다.
- OpenAI hosted tools(`web_search`, `file_search` 등)는 먼저 `responders.py`의 provider 경계에서 route-specific policy로 붙입니다.
- 직접 만든 Python tool은 별도 `tools.py`에 구현하고, `responders.py`에서 필요한 route에만 bind합니다.
- tool workflow가 여러 단계의 상태 전이, retry, interrupt, 별도 검증을 필요로 하면 그때 LangGraph node로 승격합니다.
- 각 에이전트 폴더에는 한국어 `README.md`와 영어 `README.en.md`를 함께 둡니다.

## 라우트 라벨

| 라우트 라벨 | v0 의미 | 예시 요청 문구 |
| --- | --- | --- |
| `general_assistant` | 일반 어시스턴트 요청 분류 | “Hello, what can you do?” |
| `learning_coach` | 학습 계획과 기술 개발 분류 | “Help me study LangGraph step by step.” |
| `research_helper` | 자료 탐색 또는 리서치 계획 분류 | “Find sources about FastAPI testing.” |
| `project_planner` | 프로젝트 계획, 마일스톤, 구현 순서 분류 | “Plan my next backend milestone.” |
| `career_helper` | 이력서, 리크루터 대상 문구, 커리어 자료 분류 | “Improve my resume bullet.” |

## 설정

uv로 의존성을 설치합니다.

```bash
uv sync
```

로컬에서 기본 OpenAI 응답 모드를 실행하려면 환경 설정 파일을 만듭니다.

```bash
cp .env.example .env
```

기본 `.env.example` 설정은 OpenAI 응답 모드를 사용하도록 되어 있습니다. 실제 실행 전에 `OPENAI_API_KEY` 줄의 주석을 해제하고 본인의 키로 바꿉니다.

```bash
MY_AGENTS_RESPONSE_MODE=openai
OPENAI_API_KEY=sk-your-project-key
MY_AGENTS_OPENAI_MODEL=gpt-5.5
```

선택적 튜닝 값은 다음과 같습니다.

```bash
MY_AGENTS_OPENAI_MAX_OUTPUT_TOKENS=1200
MY_AGENTS_OPENAI_TIMEOUT_SECONDS=30
# GPT-5 계열 튜닝, 선택 사항:
# MY_AGENTS_OPENAI_REASONING_EFFORT=low
# MY_AGENTS_OPENAI_VERBOSITY=low

# 문서 embedding은 기본적으로 deterministic/offline입니다.
# provider-backed JSON embedding이 필요할 때만 MY_AGENTS_EMBEDDING_MODE=openai로 설정합니다.
MY_AGENTS_EMBEDDING_MODE=deterministic
MY_AGENTS_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
# MY_AGENTS_OPENAI_EMBEDDING_DIMENSIONS=
MY_AGENTS_EMBEDDING_BATCH_SIZE=32
MY_AGENTS_OPENAI_EMBEDDING_TIMEOUT_SECONDS=30
```

포트폴리오용 채팅 서비스 로드맵을 위한 서비스 기반 설정도 포함되어 있습니다.
SQLite는 로컬 기본값이며, 아래 값들은 persistence와 first-party session 경계를 정의합니다.

```bash
MY_AGENTS_DATABASE_URL=sqlite+pysqlite:///:memory:
# 비워 두면 in-memory SQLite에서만 자동 테이블 생성을 허용합니다.
# Postgres/Neon은 Alembic migration을 사용합니다.
# MY_AGENTS_AUTO_CREATE_TABLES=
# MY_AGENTS_TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/my_agents_test
MY_AGENTS_SESSION_COOKIE_NAME=my_agents_session
MY_AGENTS_SESSION_COOKIE_SECURE=true
MY_AGENTS_SESSION_COOKIE_SAMESITE=lax
MY_AGENTS_CSRF_HEADER_NAME=X-CSRF-Token
# 기본값은 비활성화입니다. local deterministic frontend demo에서만 켭니다.
MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED=false
# Backend-owned public-demo signup kill switch입니다. 기존 login/session 동작은 유지됩니다.
MY_AGENTS_AUTH_SIGNUP_ENABLED=true
# Provider-free guest access는 기본 비활성화이며, 활성화해도 server-side limit을 적용합니다.
MY_AGENTS_GUEST_ACCESS_ENABLED=false
MY_AGENTS_GUEST_CODE_TTL_SECONDS=900
MY_AGENTS_GUEST_ACCESS_TTL_SECONDS=86400
MY_AGENTS_GUEST_MAX_CONVERSATIONS=1
MY_AGENTS_GUEST_MAX_PROMPTS=5
MY_AGENTS_GUEST_MAX_DOCUMENT_UPLOADS=3
MY_AGENTS_DEPLOYMENT_ENVIRONMENT=local
MY_AGENTS_AUTH_EMAIL_MODE=smtp
MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL=http://localhost:3000
MY_AGENTS_AUTH_SMTP_HOST=smtp.resend.com
MY_AGENTS_AUTH_SMTP_PORT=587
MY_AGENTS_AUTH_SMTP_USERNAME=resend
MY_AGENTS_AUTH_SMTP_PASSWORD=REPLACE_WITH_RESEND_API_KEY
MY_AGENTS_AUTH_SMTP_FROM_EMAIL=REPLACE_WITH_VERIFIED_RESEND_FROM_EMAIL
MY_AGENTS_AUTH_SMTP_USE_STARTTLS=true
MY_AGENTS_AUTH_SMTP_TIMEOUT_SECONDS=10
# browser cookie 요청을 허용할 frontend origin을 comma-separated로 명시합니다.
# MY_AGENTS_CORS_ALLOWED_ORIGINS=http://localhost:3000
MY_AGENTS_AUTH_ABUSE_PROTECTION_ENABLED=true
MY_AGENTS_AUTH_ABUSE_MAX_ATTEMPTS=20
MY_AGENTS_AUTH_ABUSE_WINDOW_SECONDS=900
```

`.env`와 `.env.*`는 git에서 제외됩니다. `.env.example`에는 실제 비밀값이 없으므로 커밋해도 안전합니다.

별도 browser frontend를 붙일 때는 `MY_AGENTS_CORS_ALLOWED_ORIGINS`에 정확한 frontend
origin을 넣고, frontend 요청은 `credentials: "include"`로 보내야 합니다. 이 백엔드는
앱 소유 browser cookie를 사용하므로 wildcard CORS origin은 거부합니다. 로컬 SQLite demo
명령, dev auth outbox, cookie/CSRF 기대사항, SSE/run detail contract는
[frontend demo runbook](./docs/portfolio-chat-service/10-frontend-demo-runbook.md)을 참고하세요. strict V1 마무리 작업은
[V1 contract freeze and evidence map](./docs/portfolio-chat-service/11-v1-phase-0-contract-freeze-evidence-map.md)에서 시작합니다.
local direct-browser CORS에서는 hostname을 맞추세요. `localhost:3000` frontend는
`localhost:8000` backend와, `127.0.0.1:3000` frontend는 `127.0.0.1:8000` backend와
짝지어야 browser cookie가 일관되게 전송됩니다.
배포된 frontend/backend가 cross-site라서 `MY_AGENTS_SESSION_COOKIE_SAMESITE=none`이
필요하다면 `MY_AGENTS_SESSION_COOKIE_SECURE=true`를 유지해야 합니다. 설정 계층은
`SameSite=None`과 insecure cookie 조합을 거부합니다.

### Postgres/Neon과 Alembic migration

로컬 테스트 기본값은 빠르고 credential-free인 SQLite in-memory 데이터베이스입니다. 실제 서비스 데모나 배포형 실행에서는 Postgres/Neon을 사용하고, 테이블 생성은 SQLAlchemy `create_all`이 아니라 Alembic migration이 담당합니다.

```mermaid
flowchart LR
    Models["SQLAlchemy models"] --> Alembic["Alembic migration files"]
    Alembic --> Postgres[("Postgres / Neon schema")]
    Tests["Offline tests"] --> SQLite[("SQLite in-memory auto-create")]
```

Neon을 사용할 때는 `.env`에 본인의 연결 문자열을 로컬로만 넣습니다. `sslmode=require`를 포함한 URL 형태를 사용하고, 실제 URL은 문서나 커밋에 남기지 않습니다.

```bash
MY_AGENTS_DATABASE_URL=postgresql+psycopg://user:password@host/dbname?sslmode=require
MY_AGENTS_RESPONSE_MODE=deterministic
uv run alembic upgrade head
```

전용 테스트 데이터베이스가 있을 때만 Postgres/Neon migration smoke test를 실행합니다.
값이 없으면 해당 테스트는 자동으로 skip됩니다.

```bash
MY_AGENTS_TEST_DATABASE_URL=postgresql+psycopg://user:password@host/test_db?sslmode=require \
uv run pytest tests/test_migrations.py -q
```

Neon 콘솔이 보여주는 샘플 `playing_with_neon` 테이블 쿼리는 이 앱 스키마와 무관하므로 실행하지 않아도 됩니다. 이 프로젝트의 테이블은 Alembic migration으로 생성합니다.

Neon 없이 로컬에서 pgvector를 테스트하려면 Docker helper를 사용할 수 있습니다. 기본값은
DockerHub의 `pgvector/pgvector:pg17` 이미지를 pull하고, Postgres를 `127.0.0.1:5433`에
띄우며, git-ignored `.env.pgvector.local` 파일을 생성하고 migration까지 실행할 수 있습니다.
생성된 로컬 env 파일은 `MY_AGENTS_AUTH_EMAIL_MODE=local`과
`MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED=true`를 강제해서 signup verification이 SMTP/Resend가 아니라
dev outbox를 사용하게 합니다.
VS Code에는 같은 helper를 pre-launch task로 실행한 뒤 backend를 시작하는
`FastAPI: uvicorn main:app (local pgvector)` launch configuration도 포함되어 있습니다.

```bash
uv run python -m scripts.dev_pgvector up --migrate
set -a; source .env.pgvector.local; set +a
MY_AGENTS_RESPONSE_MODE=deterministic uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

이 로컬 Postgres로 전환한 뒤에는 document를 다시 ingest해야 `embedding_vector`가 채워집니다.
pgvector schema migration은 다음 명령으로 확인할 수 있습니다.

```bash
uv run python -m scripts.dev_pgvector test
```

SQLAlchemy, Postgres, Alembic의 관계를 짧게 정리하면 다음과 같습니다.

- **SQLAlchemy**: Python 코드에서 테이블/관계를 다루는 ORM과 DB 접근 도구입니다.
- **Postgres/Neon**: 실제 데이터를 저장하는 데이터베이스입니다. Neon은 managed Postgres 서비스입니다.
- **Alembic**: SQLAlchemy model 변화와 실제 DB schema 변화를 연결하는 migration ledger입니다.

### Generic container deployment path

이 저장소에는 hosted public portfolio demo를 위한 provider-neutral `Dockerfile`과
`.dockerignore`가 포함되어 있습니다. 이 파일들은 host 선택, secret 설정, hosted
migration 실행, 유료 provider 활성화를 자체적으로 수행하지 않습니다.

로컬 container build/run 예시는 다음과 같습니다.

```bash
docker build -t my-agents-backend .
docker run --rm --env-file .env -p 8000:8000 my-agents-backend
```

Container는 FastAPI를 다음 명령으로 시작합니다.

```bash
uv run --no-sync uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Reviewer-facing preview/public demo 전에는
[generic container deployment path](./docs/portfolio-chat-service/13-generic-container-deployment-path.md)와
[public demo deployment readiness runbook](./docs/portfolio-chat-service/12-public-demo-deployment-readiness.md)을
따르세요. 두 문서는 env var 이름, Alembic migration command, `/health`와
`/openapi.json` smoke check, preview-vs-production guardrail, rollback/disable path,
그리고 secret, provider activation, hosted database migration, public URL, spend에
대한 owner-gated boundary를 설명합니다.

## 검사 실행

자동화된 테스트를 실행합니다.

```bash
uv run pytest
```

Ruff 검사를 실행합니다.

```bash
uv run ruff check .
```


## 개인 학습 로그

학습 경로를 지원하는 자료는 [`docs/learning/`](./docs/learning/README.md)에 둡니다. root numbered note는 개인 학습 로그 순서로 유지하고, [`docs/learning/agent-lab/`](./docs/learning/agent-lab/README.md)처럼 focused track은 하위 폴더에 둘 수 있습니다. 주로 project architecture를 설명하는 문서는 별도로 [`docs/portfolio-chat-service/`](./docs/portfolio-chat-service/README.md)에 둡니다.

명시적으로 개인 학습 노트를 저장하고 싶을 때는 다음 helper를 사용합니다.

```bash
uv run python scripts/learning_log.py \
  --title "Python syntax catch-up: *, Iterable, and **" \
  --body-file /tmp/learning-note.md \
  --topic python \
  --related-code my_agents/agents/general_assistant/responders.py
```

## 로컬에서 API 실행

FastAPI 개발 서버를 시작합니다.

```bash
uv run fastapi dev main.py
```

FastAPI CLI를 사용할 수 없거나 동작이 바뀐 경우의 대안입니다.

```bash
uv run uvicorn main:app --reload
```

기본적으로 로컬 API는 `http://127.0.0.1:8000`에서 사용할 수 있습니다.

## 터미널에서 채팅하기

FastAPI 서버를 시작하지 않고 general assistant graph를 직접 실행할 수 있습니다.

```bash
uv run python -m my_agents.cli
```

그다음 터미널에서 메시지를 입력합니다.

```text
You: Help me study LangGraph
Assistant: Classified as route label `learning_coach`...
You: /exit
Goodbye.
```

터미널 채팅은 OpenAI 모드에서 LangGraph streaming을 사용해 토큰이 생성되는 대로 출력합니다. deterministic 모드에서는 같은 스트리밍 경로로 최종 그래프 업데이트를 출력합니다. 메시지 히스토리는 현재 프로세스 안에서만 유지되며 영속화하지 않습니다.

## API 예시

아래 응답 예시는 문서와 테스트에서 안정적으로 비교할 수 있도록 deterministic 모드 기준입니다. 기본 OpenAI 모드에서는 `reply` 문구가 모델 응답에 따라 달라질 수 있습니다.

### `GET /health`

요청:

```bash
curl http://127.0.0.1:8000/health
```

응답 예시:

```json
{
  "status": "ok",
  "service": "my-agents",
  "version": "0.1.0"
}
```

### First-party auth 기반

포트폴리오용 채팅 서비스 로드맵을 위해 first-party auth/session 및 account lifecycle
기반이 추가되었습니다. 현재 범위는 email/password signup, local/dev email verification,
verified-email login, 앱이 소유하는 opaque session, logout용 CSRF proof, `/auth/me`,
password reset request/confirm endpoint, 그리고 signup/login/verification-token/password-reset
남용을 막기 위한 local in-process attempt limiter입니다.

이것이 전체 production-grade RAG 서비스가 완성되었다는 뜻은 아닙니다.
하지만 auth, group/document permission, server-owned conversation, text KB ingestion,
permission-aware retrieval, JSON-backed semantic embedding ranking, citation-backed answer
composition, structured agent activity event, JSON/SQLite fallback을 유지하는 Postgres pgvector
vector search, SSE conversation-run stream의 얇은 end-to-end 흐름은 구현되어 있습니다.
production parser, ANN/vector index tuning, cross-encoder reranking은 이후 마일스톤입니다.

구현된 auth endpoint:

- `POST /auth/signup`
- `POST /auth/verify-email`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `POST /auth/password-reset/request`
- `POST /auth/password-reset/confirm`
- `POST /auth/guest/request`
- `POST /auth/guest/login`
- `MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED=true`일 때만 `GET /auth/dev/outbox`

Signup은 안전한 user data와 `verification_email_sent`를 반환합니다. 기본 auth email sender는
테스트/개발용 offline local boundary라서 local v0 작업에는 유료 email provider가 필요하지 않습니다.
Preview/public visitor account를 열 때는 `MY_AGENTS_AUTH_EMAIL_MODE=smtp`와
`MY_AGENTS_AUTH_PUBLIC_APP_BASE_URL`, SMTP provider 설정을 사용합니다. 이 generic SMTP
boundary는 provider-specific SDK 없이 실제 verification/reset email을 보낼 수 있게 합니다.
`MY_AGENTS_DEPLOYMENT_ENVIRONMENT=production`은 local email mode와
`MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED=true`를 거부합니다. Login은 `email_verified_at`이 설정된
뒤에만 성공합니다. Password reset request는 계정 존재 여부와 관계없이 동일한 accepted
response를 반환하므로 account enumeration을 피합니다.
`MY_AGENTS_AUTH_SIGNUP_ENABLED=false`를 설정하면 기존 verified user의 login/session
동작은 유지하면서 backend에서 새 public signup만 막을 수 있습니다.

Provider-free public-demo guest access는 `MY_AGENTS_GUEST_ACCESS_ENABLED=true`일 때만
사용할 수 있습니다. `POST /auth/guest/request`는 짧게 유효한 one-time code를 JSON으로
직접 반환하고, `POST /auth/guest/login`은 그 code를 한 번만 사용해 일반 app session
cookie와 `csrf_token`을 발급합니다. Guest user는 명시적인 ephemeral identity이며 auth
응답에서 `email: null`, `is_guest: true`, `guest_expires_at`을 반환합니다. Guest limit
기본값은 24시간 access, conversation 1개, prompt 5개, document create/upload 3개입니다.
Limit 실패는 안전한 `403` 또는 `429` JSON detail로 반환합니다.

deterministic local frontend demo에서는 `MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED=true`로
in-memory auth email outbox를 `/auth/dev/outbox`에 노출할 수 있습니다. UI가 verification/reset
token을 읽기 위한 local demo 전용 기능이므로 local demo 밖에서는 꺼둡니다.

local V1 demo에서는 verified demo user, text document, extraction run을 한 번에 seed할 수 있습니다:

```bash
MY_AGENTS_RESPONSE_MODE=deterministic \
MY_AGENTS_DATABASE_URL=sqlite+pysqlite:///./local-demo.db \
MY_AGENTS_AUTO_CREATE_TABLES=true \
uv run python -m scripts.local_demo_seed
```

seeded credential은 `test@test.com` / `correct horse battery staple`입니다. 이 helper는
in-memory 및 non-SQLite database URL을 거부하며, `--reset-database`는 dev server를 멈춘
뒤에만 사용하세요.

backend 실행 후 frontend 없이 V1 API path를 검증하려면 다음 smoke helper를 사용합니다:

```bash
uv run python -m scripts.local_demo_smoke --base-url http://localhost:8000
```

이 smoke는 public API call만 사용해 health, seeded login, text document ingestion, SSE chat,
persisted citations, redacted run events를 확인합니다. bodyless ingest endpoint를 검증하기
때문에 실행할 때마다 local extraction run이 하나 추가됩니다.

Auth abuse protection은 v0에서 의도적으로 local/replaceable boundary입니다. Bucket key는
digest로 저장되고, `MY_AGENTS_AUTH_ABUSE_*` 설정으로 제한을 조정하며, offline test가 이
동작을 검증합니다. 이것은 single-process public demo boundary이며 multi-worker production
보호라고 주장하면 안 됩니다. 향후 public deployment나 multi-worker 구성이 필요해지면 같은
boundary를 Redis, gateway, shared store로 교체할 수 있습니다.

별도 frontend가 browser에서 실행될 때는 `MY_AGENTS_CORS_ALLOWED_ORIGINS`로 정확한 origin을
설정합니다. Login은 `HttpOnly` session cookie를 설정하므로 frontend 요청은 credentialed
fetch를 사용해야 하고, logout은 login 응답에서 받은 CSRF token을 설정된 header로 보내야 합니다.
현재 cookie-authenticated CSRF 필수 mutation은 `POST /auth/logout`입니다.

signup 요청 예시:

```bash
curl -X POST http://127.0.0.1:8000/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"correct horse battery staple"}'
```

`/auth/login`은 email verification 이후 안전한 user data와 `csrf_token`을 반환하고 설정된
session cookie를 설정합니다. password, password hash, raw auth-token hash는 API 응답으로
반환하지 않습니다.

### Group 및 document permission 기반

백엔드에는 첫 번째 authorization slice도 포함되어 있습니다.

- `POST /groups`, `GET /groups`, `GET /groups/{group_id}`
- `POST /groups/{group_id}/members`
- `PATCH /groups/{group_id}/members/{user_id}`
- `POST /documents`, `GET /documents`, `GET /documents/{document_id}`
- `PATCH /documents/{document_id}/permissions`

현재 permission 모델은 의도적으로 작지만 테스트로 보호됩니다. personal owner는
자기 문서를 관리할 수 있고, group owner/admin은 group access를 관리할 수 있으며,
group viewer는 읽기만 가능하고, non-member는 group document에 대해 안전한 거부
응답을 받습니다.

### 서버 소유 conversation

제품용 chat surface는 client가 보낸 `history`에 의존하는 방식에서 서버가 소유하는
conversation run으로 이동하고 있습니다. 현재 conversation surface는 다음과 같습니다.

- `POST /conversations`
- `GET /conversations`
- `GET /conversations/{conversation_id}`
- `POST /conversations/{conversation_id}/messages`
- `GET /conversations/{conversation_id}/messages`
- `POST /conversations/{conversation_id}/messages/{message_id}/replay`
- `POST /conversations/{conversation_id}/runs`
- `POST /conversations/{conversation_id}/runs/stream`
- `GET /conversations/{conversation_id}/runs`

`/conversations/{conversation_id}/runs`는 user message를 저장하고, 서버가 소유하는
conversation history와 principal/conversation context를 현재 LangGraph assistant에
전달합니다. 먼저 deterministic retrieval routing policy가 `no_retrieval`,
`retrieval_required`, `retrieval_optional`, `clarification_required` 중 하나를 선택합니다.
검색이 필요한 경우에만 `RetrievalService`가 권한이 확인된 document chunk를 검색하고,
entity mention으로 연결된 권한 내 관련 chunk를 확장합니다. 응답은 `answer_mode`
(`general_knowledge`, `document_grounded`, `mixed`)와 citation을 함께 저장/반환합니다.
기존 `/assistant/chat` endpoint는 legacy/dev smoke surface로 남아 있으며, personal/group
KB 접근을 위한 제품용 chat surface가 되어서는 안 됩니다.

`/conversations/{conversation_id}/runs/stream`은 같은 product run을
`text/event-stream` Server-Sent Events로 노출합니다. 먼저 server `run_id`를 담은
`run_started`를 보내고, `user_message_stored`, `retrieval_completed`, `graph_invoked`,
`answer_composed` 같은 redacted progress event를 보냅니다. assistant text는
`answer_delta` event로 점진적으로 보낸 뒤, `/runs`와 같은 응답 shape를 담은 최종
`run_completed` event를 보냅니다. stream 시작 후 graph 실행이 실패하면 failed run을
저장하고 raw prompt나 provider exception text를 노출하지 않는 `run_failed` 및
`run_error` event를 보냅니다.
프론트엔드는 `GET /conversations/{conversation_id}/messages`로 서버가 저장한 transcript를
권한 확인 후 다시 읽고, `GET /conversations/{conversation_id}/runs`로 completed/failed/cancelled
run history를 확인할 수 있습니다.

`POST /conversations/{conversation_id}/messages/{message_id}/replay`는 기존 assistant message를
linear transcript 안에서 다시 생성합니다. body는 생략하거나 `{}`를 보낼 수 있으며, 원래
assistant message와 연결된 run이 있으면 그 run의 `knowledge_base_selection`을 보존합니다.
원래 run을 찾을 수 없는 legacy/orphan message일 때만 optional
`knowledge_base_selection` fallback을 사용하고, 없으면 일반 conversation run 기본값인
`mode: "all"`을 사용합니다. V1 replay는 branch를 만들지 않습니다. 대상 assistant message와
그 뒤의 message/run/event/citation을 삭제한 뒤, 대상 직전의 user turn을 prompt로 삼고 그 이전
history를 함께 전달해 새 run을 생성합니다. 응답 shape는 `/runs`와 같은
`ConversationRunResponse`입니다. 존재하지 않거나 권한 없는 conversation은 `404`
`detail: "conversation not found"`, 존재하지 않거나 다른 conversation의 message는 `404`
`detail: "message not found"`, user message replay는 `409`
`detail: "message is not an assistant message"`, active run 중 replay는 `409`
`detail: "conversation run already active"`입니다.

명시적인 send-immediately steering은 `run_started`의 `run_id`로
`POST /conversations/{conversation_id}/runs/{run_id}/cancel`을 호출하고, `run_cancelled` 또는
stream 종료를 기다린 뒤 새 message를 제출합니다. 백엔드는 같은 conversation의 parallel active
run을 `409` 및 `detail: "conversation run already active"`로 거부합니다. cancelled run은 partial
assistant text나 citation을 저장하지 않으며, 중단된 user prompt는 guest prompt limit에 그대로
포함됩니다.

### Knowledge-base ingestion 기반

Knowledge base는 사용자에게 보이는 document library 추상화입니다. 사용자는 KB를 만들고,
그 KB에 파일/문서를 추가한 뒤 ingest하며, chat 시 assistant가 검색할 KB를 선택합니다.
사용자-facing client의 canonical 경로는 KB-nested API입니다.

- `POST /knowledge-bases` — personal KB 생성 또는 owner/admin 전용 추가 group KB 생성
- `GET /knowledge-bases`
- `GET /knowledge-bases/{knowledge_base_id}`
- `GET /knowledge-bases/{knowledge_base_id}/documents`
- `POST /knowledge-bases/{knowledge_base_id}/documents` — 특정 personal KB 안에 JSON 텍스트 문서 생성
- `POST /knowledge-bases/{knowledge_base_id}/documents/upload` — 특정 personal KB 안에 multipart PDF/Markdown/plain text 업로드
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest` — 해당 KB 안에서 bodyless 동기 ingestion 실행
- `POST /knowledge-bases/{knowledge_base_id}/documents/{document_id}/ingest/async` — `202 Accepted`와 queued extraction run을 반환하는 비동기 ingestion 시작
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs`
- `GET /knowledge-bases/{knowledge_base_id}/documents/{document_id}/extraction-runs/{run_id}` — 단일 run progress polling 경로

Legacy `/documents`, `/documents/upload` write path는 standalone/developer 호환 surface로
남지만, 이제 authorized `knowledge_base_id`가 필수이며 누락 시 `422`를 반환합니다. 존재하지
않거나 권한 없는 KB는 `404`로 conceal하고, nested document operation에서 document가 path KB에
속하지 않으면 `404`를 반환합니다.

새 그룹을 만들면 기본 group-scoped KB 하나가 함께 만들어집니다. Group owner/admin은 추가
group KB를 만들 수 있지만, 직접 document create/upload 경로는 group KB target을 거부합니다.
Group knowledge에는 personal document를 위한 명시적 publish-review workflow가 있습니다. Group
member는 자신이 소유한 personal `source_document_id`와 group-scoped
`target_knowledge_base_id`로 `POST /groups/{group_id}/publish-requests`를 만들 수 있습니다.
`GET /groups/{group_id}/publish-requests`는 owner/admin에게 모든 request를, 일반 member에게는
자기 request만 보여줍니다. `POST /groups/{group_id}/publish-requests/{request_id}/approve`와
`/reject`는 owner/admin 전용입니다. Pending/rejected request는 metadata일 뿐 retrieval 효과가
없고, approve 시 target KB 아래에 별도 group-owned document copy를 만들고 ingest합니다. 원본
personal document는 계속 private 상태로 남습니다.

Conversation run은 `knowledge_base_selection`을 받습니다. `mode: "all"`은 권한 있는 모든 KB를
검색하고, `mode: "selected"`는 전달한 KB ID만 hard retrieval boundary로 검색합니다. selected mode에
ID가 없으면 `422`, all mode에 ID가 있으면 `422`, 권한 없거나 존재하지 않는 selected KB ID는
`404`입니다. 응답, run detail, run history, stream event는 `knowledge_base_selection`과
`resolved_knowledge_base_count`를 노출합니다.
검색 결과는 같은 document 안의 중복 chunk ordinal/content를 dedupe해서, 과거 재-ingestion으로
중복 chunk가 남아 있어도 top-k citation slot을 낭비하지 않습니다. Assistant reply에는 더 이상
hard-truncated document prefix를 임의로 붙이지 않으며, grounding은 `citations`와 graph input의
retrieved context를 통해 노출합니다.

업로드 경로는 5 MiB 이하의 `.pdf`, `.md`,
`.markdown`, `.txt` 파일을 받습니다. PDF는 classify → route → extract → clean → validate 흐름으로 처리합니다.
`application/pdf`는 먼저 PyMuPDF(`pymupdf`)로 빠른 page text를 추출합니다. PyMuPDF 결과가 비어 있거나 품질 검사를 통과하지 못하면 Docling(`docling`)이 구조화된 Markdown/table 후보를 추출하는 primary fallback이 됩니다.
그 뒤에도 실패하면 Tesseract OCR fallback이 PyMuPDF로 page image를 렌더링한 뒤 `tesseract -l kor+eng --psm 6` 형태로 image-heavy PDF를 OCR합니다. OCR 뒤에도 실패하면 기존 `pypdf`, MIT 라이선스 `pdfplumber`, 단순 literal/FlateDecode deterministic stream fallback 순서로 호환성을 유지합니다. 모든 PDF 추출 결과는 PostgreSQL `text`에 저장하기 전에 NUL/control byte, 반복 locale metadata, 알려진 font boilerplate, encoding garbage 검사를 통과해야 합니다.
품질 검사를 통과하지 못한 PDF나 Docling이 `<!-- image -->` placeholder/bullet-only 구조만 반환하는 image-heavy PDF는 DB insert 500 또는 빈 chunk 저장 대신 안전한 `400` upload error로 거부됩니다. Docling은 Apple MPS의 float64 미지원 crash를 피하기 위해 기본값이 CPU accelerator, OCR off, 30초 document timeout입니다. 이 값은 `MY_AGENTS_DOCLING_ACCELERATOR`(`cpu|cuda|auto|mps|xpu`), `MY_AGENTS_DOCLING_OCR_ENABLED`, `MY_AGENTS_DOCLING_TIMEOUT_SECONDS`, `MY_AGENTS_DOCLING_THREADS`로 조정할 수 있습니다. GPU Docker production에서는 CUDA-compatible image/runtime을 준비한 뒤 `MY_AGENTS_DOCLING_ACCELERATOR=cuda`를 명시하세요. Tesseract fallback은 `MY_AGENTS_TESSERACT_ENABLED`, `MY_AGENTS_TESSERACT_LANGUAGES`, `MY_AGENTS_TESSERACT_PSM`, `MY_AGENTS_TESSERACT_RENDER_SCALE`, `MY_AGENTS_TESSERACT_TIMEOUT_SECONDS`로 조정하며 Docker image에는 `tesseract-ocr`, `tesseract-ocr-kor`, `tesseract-ocr-eng` 같은 system package가 필요합니다. PyMuPDF는 AGPL/commercial license 검토가 필요한 의존성이고, 이 프로젝트에서는 PDF extraction milestone을 위해 명시적으로 도입했습니다.
Markdown/plain text는 UTF-8 텍스트로 decoding하며 구조적 Markdown parsing은 아직 하지 않습니다. 업로드 metadata
(`source_filename`, content type, byte size, SHA-256, page count, parser name)는 document에
저장되고, ingestion chunk에는 `source_page`가 기록되어 이후 citation provenance에 사용할 수
있습니다. conversation citation 응답은 이미 가능한 경우 `source_page`와 `source_filename`을
함께 반환합니다. ingestion은 paragraph/sentence 기반 chunk, entity mention, JSON-backed embedding을 생성합니다.
기존 동기 ingestion endpoint는 호환성을 유지하고, async ingestion endpoint는 in-process background thread에서 fresh DB session으로 실행됩니다. `ExtractionRunResponse`는 `status`(`pending|running|completed|failed`), `stage`, `progress_percent`, count, safe `error`, `started_at`, `completed_at`을 반환합니다. 이 V1 async path는 외부 queue/Redis/Celery 없는 local/demo 계약이며 process restart durability는 보장하지 않습니다.
Postgres에서 Alembic migration을 적용하면 chunk에 pgvector `embedding_vector`도 저장되어,
retrieval이 권한 필터가 적용된 SQL vector search를 먼저 수행하고 JSON cosine ranking으로 fallback할 수 있습니다.
기본값은 offline test용 32차원 deterministic lexical-hash vector이며,
`MY_AGENTS_EMBEDDING_MODE=openai`일 때는 `langchain-openai`/OpenAI embedding
(`text-embedding-3-small` 등)을 사용합니다. Docling dependency는 OCR 기능을 포함하지만 현재 upload contract는 request-time local extraction만 사용하므로 scanned/encrypted/image-only PDF,
복잡한 multi-column/layout 복원의 품질 보장, DOCX, HTML, CSV/JSON structural parsing은 아직 지원하지 않으며, OpenAI extraction 호출,
ANN/vector index tuning, cross-encoder reranking은 아직 수행하지 않습니다.


### Permission-aware RAG 및 citation 기반 응답

제품용 conversation run에는 retrieval routing이 포함된 permission-aware RAG slice가 포함되어 있습니다.

1. `my_agents/knowledge/routing.py`가 질문을 `no_retrieval`, `retrieval_required`,
   `retrieval_optional`, `clarification_required`로 분류합니다.
2. `no_retrieval`은 RetrievalService를 호출하지 않고 `answer_mode=general_knowledge`로 답합니다.
3. `retrieval_required`/`retrieval_optional`만 `RetrievalService`를 호출하며, 서비스는 현재 사용자에게 읽기 권한이 있는 document chunk 후보만 먼저 선택합니다.
4. 설정된 provider로 query embedding을 만들고, Postgres에서는 권한이 확인된 row set 안에서 pgvector SQL vector search로 top-k 후보를 먼저 좁힙니다. SQLite/tests 또는 pgvector 후보가 없으면 기존 JSON-backed cosine similarity fallback을 사용합니다. 최종 후보는 lexical score와 섞어 ranking하며, personal-document 질문은 최신 권한 내 chunk fallback을 사용할 수 있습니다.
5. 직접 검색된 chunk의 entity mention을 기준으로, 같은 entity를 공유하는 권한 내 chunk를 graph expansion context로 추가합니다.
6. optional 검색 결과가 관련 있으면 `answer_mode=mixed`, required 검색 결과가 관련 있으면 `answer_mode=document_grounded`가 됩니다. 관련 context가 없으면 일반 지식 답변으로 남고 citation을 만들지 않습니다.
7. 권한이 확인된 compact context payload만 `general_assistant` graph/provider prompt에 전달하고, 응답 payload에는 `retrieval_route`, `answer_mode`, `document_scope`, `citations`를 함께 반환합니다.

예시 응답 일부:

```json
{
  "run_id": "...",
  "reply": "Based on authorized knowledge context:\n- Private RAG Plan: ...",
  "handled_by": "personal_assistant_graph",
  "retrieval_route": "retrieval_required",
  "answer_mode": "document_grounded",
  "document_scope": "unknown",
  "citations": [
    {
      "id": "...",
      "document_id": "...",
      "chunk_id": "...",
      "snippet": "Phoenix Retrieval Kernel uses LangGraph..."
    }
  ]
}
```

중요한 보안 경계는 retrieval 전에 permission filter가 먼저 실행된다는 점입니다. outsider는
같은 질문을 해도 private document chunk, citation, reply context를 받을 수 없습니다.


### Agent activity event 및 deterministic eval fixture

conversation run은 hidden chain-of-thought를 노출하지 않고, UI가 보여줄 수 있는 구조화된
activity event를 저장합니다.

- `GET /conversations/{conversation_id}/runs/{run_id}/events`
- `GET /conversations/{conversation_id}/runs/{run_id}`

현재 이벤트는 run start, user message 저장, permission-aware retrieval 완료, graph invoke,
answer composition 단계를 순서대로 보여줍니다. streaming endpoint는 request 중에도 같은
high-level event vocabulary와 점진적인 assistant text용 `answer_delta` chunk를 전송합니다.
graph 실행이 실패하면 failed run과 `run_failed` event를 저장하되, payload에는 safe error type만
남깁니다. 프론트엔드가 streaming run을 명시적으로 cancel하면 `run_cancel_requested`/
`run_cancelled` event를 저장하고 partial assistant text는 저장하지 않습니다. payload에는 raw message,
document content, secret token을 넣지 않고 count, route label, latency 같은 redacted
metadata만 둡니다.

완료된 run detail은 refresh-safe입니다. `GET /conversations/{id}/runs/{run_id}`는 완료된
run의 persisted reply, route, citations를 반환합니다. 실패하거나 취소된 run은 완료된
reply/citation payload가 없으므로 conflict를 반환합니다.

또한 `my_agents/agent_runtime/evals.py`에는 grounding/citation, permission leakage,
event redaction, latency budget을 확인하는 deterministic eval helper가 있습니다. 이 eval은
production 평가 시스템이 아니라, 포트폴리오에서 중요한 안전 경계를 테스트로 설명하기 위한
fixture입니다.


### Portfolio demo flow

포트폴리오 데모에서는 `/assistant/chat`보다 product surface인 conversation run을 우선 보여주는 것이 좋습니다.

1. `uv run python -m scripts.local_demo_seed`로 local verified demo account를 seed하거나, `/auth/signup`, local demo의 `/auth/dev/outbox`, `/auth/verify-email`, `/auth/login`으로 session을 만든다.
2. `/groups`로 group을 만들고 필요하면 member를 추가한다.
3. `/documents`로 personal 또는 group document를 만들고 `/documents/{id}/permissions`로 명시적 권한을 부여한다.
4. `/documents/{id}/ingest`로 chunk/entity/relationship을 생성한다.
5. `/conversations`로 thread를 만들고 `/conversations/{id}/runs`로 질문한다.
6. `/conversations/{id}/runs/{run_id}`의 persisted `citations`와 `/conversations/{id}/runs/{run_id}/events`를 함께 보여줘서 grounding과 agent activity를 설명한다.

### `POST /assistant/chat`

요청:

```bash
curl -X POST http://127.0.0.1:8000/assistant/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Help me study LangGraph routing","history":[]}'
```

응답 예시:

```json
{
  "reply": "Classified as route label `learning_coach`. Capability mode `simulation`; `simulated_learning_coach` is a toy learning/test capability, not a real-world integration. A useful learning path is to define the concept, build a tiny example, then test it. This backend is running in deterministic response mode.",
  "route": {
    "label": "learning_coach",
    "explanation": "This request is about study planning, practice, or skill development."
  },
  "handled_by": "personal_assistant_graph"
}
```

이 요청은 `learning_coach` 라우트 라벨로 분류됩니다. 라벨은 메타데이터일 뿐이며, v0에서는 별도의 학습 기능이 실행되지 않습니다.

다른 요청 예시:

```bash
curl -X POST http://127.0.0.1:8000/assistant/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Plan my next backend milestone","history":[]}'
```

응답 예시:

```json
{
  "reply": "Classified as route label `project_planner`. Capability mode `simulation`; `simulated_project_planner` is a toy learning/test capability, not a real-world integration. A useful planning pass is to name the goal, split the next milestone, and define verification evidence. This backend is running in deterministic response mode.",
  "route": {
    "label": "project_planner",
    "explanation": "This request is about planning project work, milestones, scope, or next steps."
  },
  "handled_by": "personal_assistant_graph"
}
```

이 요청은 `project_planner` 라우트 라벨로 분류됩니다. 라벨은 메타데이터일 뿐이며, v0에서는 별도의 계획 기능이 실행되지 않습니다.

## 요청 형태

```json
{
  "message": "Help me plan my LangGraph study project",
  "history": []
}
```

필드:

- `message`: 필수이며 비어 있지 않은 문자열입니다.
- `history`: 선택적 배열이며, 생략하면 `[]`가 기본값입니다.
- `history[].role`: `user` 또는 `assistant`입니다.
- `history[].content`: 비어 있지 않은 문자열입니다.

빈 메시지는 그래프 실행 전에 검증/클라이언트 오류를 반환해야 합니다.

## 응답 형태

```json
{
  "reply": "...",
  "route": {
    "label": "learning_coach",
    "explanation": "This request is about study planning and skill development."
  },
  "handled_by": "personal_assistant_graph"
}
```

필드:

- `reply`: 선택된 response provider가 구성한 응답 텍스트입니다.
- `route.label`: 지원되는 라우트 라벨 중 하나입니다.
- `route.explanation`: 분류에 대한 결정론적 설명입니다.
- `handled_by`: 단일 어시스턴트 그래프 경로를 나타내는 그래프 메타데이터입니다.

## OpenAI GPT variant 설정

OpenAI 모드는 LangChain의 전용 `langchain-openai` 통합 패키지와 `ChatOpenAI`를 사용합니다. 모델은 `MY_AGENTS_OPENAI_MODEL`로 선택하므로 코드 변경 없이 GPT variant를 바꿔볼 수 있습니다.

```bash
MY_AGENTS_RESPONSE_MODE=openai
OPENAI_API_KEY=sk-your-project-key
MY_AGENTS_OPENAI_MODEL=gpt-5.5
```

현재 기본 모델 slug는 `gpt-5.5`입니다. 다른 GPT variant를 선택하면 해당 값이 `ChatOpenAI`로 직접 전달됩니다.

## 향후 확장 방향

향후 버전은 이 기반 위에 다음을 추가할 수 있습니다.

- `previous_response_id` 또는 replayed response items를 통한 OpenAI 대화 상태 유지
- 영속적인 대화 thread 또는 checkpointer
- human-in-the-loop interrupt
- 기존 라우트 라벨 분류 체계 뒤에 실제 전문 어시스턴트 기능 추가
- 필요한 경우 별도 범위나 별도 저장소의 프론트엔드

전문 기능이 명시적으로 구현되기 전까지 v0 라우트 라벨은 결정론적 분류 정보로만 남습니다.
