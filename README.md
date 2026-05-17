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

## 명시적인 v0 안내

v0의 기본 응답 모드는 OpenAI 기반입니다. 채팅 응답 생성을 실행하려면 `OPENAI_API_KEY`가 필요합니다. 테스트와 오프라인 확인을 위해 `MY_AGENTS_RESPONSE_MODE=deterministic` 모드는 계속 유지합니다.

분류는 계속 결정론적으로 수행되며, 선택한 OpenAI GPT 모델은 최종 응답 텍스트만 생성합니다. API 키 없이 실행해야 하는 경우에는 deterministic 모드로 전환합니다. v0의 라우트 라벨은 향후 범주를 위한 분류 정보일 뿐이며, 현재 product RAG/permission/event 기능은 별도 전문 에이전트가 아니라 API/service layer가 LangGraph 실행 주변에서 제공하는 기능입니다.

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

현재 그래프는 `my_agents/agents/general_assistant/` 아래에 있으며 하나의 어시스턴트/라우터 흐름을 가집니다. 분류는 결정론적으로 수행됩니다. 응답 노드는 provider 인터페이스를 통해 라우트별 응답을 구성합니다. 기본값은 `langchain-openai`의 `ChatOpenAI`이며, 오프라인/테스트용으로 결정론적 템플릿 모드를 사용할 수 있습니다. 라우트별 응답 노드는 별도의 에이전트가 아닙니다.


## 제품 서비스 표면

```mermaid
flowchart TD
    Frontend["Separate frontend"] --> Auth["/auth session"]
    Frontend --> Groups["/groups memberships"]
    Frontend --> Docs["/documents + permissions"]
    Frontend --> KB["/knowledge-bases + ingest"]
    Frontend --> Runs["/conversations/{id}/runs"]
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
5. 조건부 그래프 엣지가 라우트 라벨에 맞는 응답 구성 노드를 선택합니다.
6. 선택된 response provider가 응답을 구성합니다.
7. 그래프가 `reply`, `route.label`, `route.explanation`, `handled_by`를 포함한 타입이 지정된 응답을 반환합니다.

`handled_by`는 단일 그래프 경로(`personal_assistant_graph`)를 식별합니다. 전문 에이전트를 식별하는 값이 아닙니다.

## 에이전트 구현 패턴

새 에이전트는 `my_agents/agents/<agent_name>/` 아래에 독립된 폴더로 추가합니다. 현재 예시는 [`my_agents/agents/general_assistant/`](./my_agents/agents/general_assistant/README.md)입니다.

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
```

포트폴리오용 채팅 서비스 로드맵을 위한 서비스 기반 설정도 포함되어 있습니다.
SQLite는 로컬 기본값이며, 아래 값들은 persistence와 first-party session 경계를 정의합니다.

```bash
MY_AGENTS_DATABASE_URL=sqlite+pysqlite:///:memory:
# MY_AGENTS_TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/my_agents_test
MY_AGENTS_SESSION_COOKIE_NAME=my_agents_session
MY_AGENTS_SESSION_COOKIE_SECURE=true
MY_AGENTS_SESSION_COOKIE_SAMESITE=lax
MY_AGENTS_CSRF_HEADER_NAME=X-CSRF-Token
```

`.env`와 `.env.*`는 git에서 제외됩니다. `.env.example`에는 실제 비밀값이 없으므로 커밋해도 안전합니다.

## 검사 실행

자동화된 테스트를 실행합니다.

```bash
uv run pytest
```

Ruff 검사를 실행합니다.

```bash
uv run ruff check .
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

포트폴리오용 채팅 서비스 로드맵을 위해 최소한의 first-party auth/session 기반이
추가되었습니다. 현재 범위는 email/password signup, login, 앱이 소유하는 opaque
session, logout용 CSRF proof, `/auth/me`입니다.

이것이 전체 production-grade RAG 서비스가 완성되었다는 뜻은 아닙니다.
하지만 auth, group/document permission, server-owned conversation, text KB ingestion,
permission-aware retrieval, citation-backed answer composition, structured agent activity
event의 얇은 end-to-end 흐름은 구현되어 있습니다. streaming, production parser, pgvector
ranking은 이후 마일스톤입니다.

deterministic 모드 기준 smoke flow 예시:

```bash
curl -X POST http://127.0.0.1:8000/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"correct horse battery staple"}'

curl -i -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"correct horse battery staple"}'
```

`/auth/login`은 안전한 user data와 `csrf_token`을 반환하고 설정된 session cookie를
설정합니다. password와 password hash는 API 응답으로 반환하지 않습니다.

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
- `POST /conversations/{conversation_id}/runs`

`/conversations/{conversation_id}/runs`는 user message를 저장하고, 서버가 소유하는
conversation history와 principal/conversation context를 현재 LangGraph assistant에
전달합니다. 이후 권한이 확인된 document chunk만 검색하고, entity mention으로 연결된
권한 내 관련 chunk를 확장한 뒤, assistant reply와 citation을 저장하고 `run_id`를
반환합니다. 기존 `/assistant/chat` endpoint는 legacy/dev smoke surface로 남아 있으며,
personal/group KB 접근을 위한 제품용 chat surface가 되어서는 안 됩니다.

### Knowledge-base ingestion 기반

첫 번째 얇은 ingestion/extraction slice가 추가되었습니다.

- `POST /knowledge-bases`
- `GET /knowledge-bases`
- `POST /documents/{document_id}/ingest`
- `GET /documents/{document_id}/extraction-runs`

현재 ingestion은 document에 이미 저장된 텍스트를 대상으로 합니다. 실행하면
결정론적 chunk, embedding fixture, extracted entity, entity mention, co-occurrence
relationship, extraction-run summary를 생성합니다. conversation run은 이 산출물을
권한 필터 뒤에서만 검색하고 citation으로 반환할 수 있습니다. 이 기능은 의도적으로
얇고 로컬 중심이며, 아직 production document parsing, OpenAI extraction, pgvector
ranking은 수행하지 않습니다.


### Permission-aware RAG 및 citation 기반 응답

제품용 conversation run에는 첫 번째 permission-aware RAG slice가 포함되어 있습니다.

1. 현재 사용자에게 읽기 권한이 있는 document chunk 후보만 먼저 선택합니다.
2. 그 후보 안에서 deterministic term score로 직접 관련 chunk를 찾습니다.
3. 직접 검색된 chunk의 entity mention을 기준으로, 같은 entity를 공유하는 권한 내 chunk를
   graph expansion context로 추가합니다.
4. assistant reply에는 권한이 확인된 context만 포함하고, 응답 payload에는 `citations`를
   함께 반환합니다.

예시 응답 일부:

```json
{
  "run_id": "...",
  "reply": "Based on authorized knowledge context:\n- Private RAG Plan: ...",
  "handled_by": "personal_assistant_graph",
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

현재 이벤트는 user message 저장, permission-aware retrieval 완료, graph invoke, answer
composition 단계를 순서대로 보여줍니다. payload에는 raw message, document content,
secret token을 넣지 않고 count, route label, latency 같은 redacted metadata만 둡니다.

또한 `my_agents/agent_runtime/evals.py`에는 grounding/citation, permission leakage,
event redaction, latency budget을 확인하는 deterministic eval helper가 있습니다. 이 eval은
production 평가 시스템이 아니라, 포트폴리오에서 중요한 안전 경계를 테스트로 설명하기 위한
fixture입니다.


### Portfolio demo flow

포트폴리오 데모에서는 `/assistant/chat`보다 product surface인 conversation run을 우선 보여주는 것이 좋습니다.

1. `/auth/signup` 및 `/auth/login`으로 session을 만든다.
2. `/groups`로 group을 만들고 필요하면 member를 추가한다.
3. `/documents`로 personal 또는 group document를 만들고 `/documents/{id}/permissions`로 명시적 권한을 부여한다.
4. `/documents/{id}/ingest`로 chunk/entity/relationship을 생성한다.
5. `/conversations`로 thread를 만들고 `/conversations/{id}/runs`로 질문한다.
6. 응답의 `citations`와 `/conversations/{id}/runs/{run_id}/events`를 함께 보여줘서 grounding과 agent activity를 설명한다.

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
  "reply": "Classified as route label `learning_coach`. A useful learning path is to define the concept, build a tiny example, then test it. This backend is running in deterministic response mode.",
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
  "reply": "Classified as route label `project_planner`. A useful planning pass is to name the goal, split the next milestone, and define verification evidence. This backend is running in deterministic response mode.",
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
