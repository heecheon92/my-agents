# general_assistant 에이전트

한국어 | [English](./README.en.md)

`general_assistant`는 현재 이 저장소의 기본 LangGraph 어시스턴트/라우터 구현입니다. 사용자의 메시지를 결정론적으로 라우트 라벨로 분류한 뒤, 선택된 응답 노드가 공통 response provider를 통해 답변을 구성합니다.

## 현재 역할

- FastAPI legacy `/assistant/chat`, 터미널 CLI, product conversation run에서 호출되는 단일 LangGraph 응답 경로입니다.
- 라우트 라벨은 응답 방식을 고르는 메타데이터입니다.
- 라우트 라벨은 `AgentCapability` metadata와 연결되어 사용 가능한 tool, data source, side effect를 정직하게 전달합니다.
- 현재 라우트별 노드는 별도의 전문 에이전트 실행을 의미하지 않습니다.
- OpenAI 응답 생성은 `langchain-openai`의 `ChatOpenAI`를 통해 수행합니다.
- deterministic 모드는 테스트와 오프라인 smoke check를 위해 유지합니다.

## 파일 구조

| 파일 | 책임 |
| --- | --- |
| `graph.py` | LangGraph `StateGraph`, 노드, 조건부 라우팅, graph state 정의 |
| `classifier.py` | LangChain messages를 읽고 결정론적 `RouteDecision` 생성 |
| `responders.py` | deterministic/OpenAI response provider, OpenAI 호출 경계, 향후 hosted tool policy 위치 |
| `__init__.py` | 패키지 경계 |

## 그래프 흐름

```mermaid
flowchart TD
    Start([START]) --> Classify["classify_request"]
    Classify --> Route{"route label"}
    Route -->|general_assistant| General["respond_general"]
    Route -->|research_helper| Research["respond_research"]
    General --> Provider["response provider"]
    Research --> Provider
    Provider --> End([END])
```

## Route label 의미

| 라벨 | 현재 의미 |
| --- | --- |
| `general_assistant` | 일반 요청, 정리, 학습/계획/커리어 도움, 다음 단계 제안 |
| `research_helper` | 리서치 질문, 자료 탐색, 출처 중심 답변 방향 |



## Capability metadata

`my_agents/agents/capabilities.py`는 route capability name, purpose, tool, data source, side effect를 기록합니다. 그래프는 classification 뒤에 이 metadata를 붙이고, `responders.py`는 deterministic reply와 OpenAI prompt에 이를 포함합니다.

이 구조는 API를 정직하게 유지합니다. 예를 들어 `research_helper` route는 OpenAI mode에서 hosted `web_search`를 사용할 수 있지만, `general_assistant` route는 별도 task database나 외부 project-management tool side effect를 주장하지 않습니다.

## Product service layer와의 관계

`general_assistant` 폴더는 graph/classifier/responder 경계를 소유합니다. Auth, group/document permission, server-owned conversation, knowledge ingestion, retrieval selection, citation, agent event는 `my_agents/api/`, `my_agents/knowledge/`, `my_agents/conversations/` 같은 서비스 레이어에서 소유합니다.

제품용 conversation run은 `general_assistant`가 prose를 작성하기 전에 retrieval과 RAG contract 작업을 실행합니다. ContextForge가 authorized evidence를 검색하고, `rag_agent`가 compact trace/grounding contract를 검증합니다. `general_assistant`는 `retrieval_route`, `answer_mode`, `document_scope`, 이미 권한 확인이 끝난 compact `retrieved_context` payload만 받습니다. Graph/provider는 이 metadata로 답변 방식을 조정하지만, vector/document storage를 직접 조회하지 않습니다. 보안 결정과 permission filter는 계속 `RetrievalService`와 API/service layer에 남습니다.

```mermaid
sequenceDiagram
    participant RunAPI as conversation run API
    participant Retrieval as ContextForge / RetrievalService
    participant RAG as rag_agent contract graph
    participant Graph as general_assistant graph
    participant Provider as response provider
    participant Events as citations / events

    RunAPI->>Retrieval: route and retrieve authorized context
    Retrieval->>RAG: redacted evidence metadata
    RAG-->>Events: verified trace stages
    Retrieval-->>Graph: retrieval_route, answer_mode, retrieved_context
    Graph->>Provider: compose with answer_mode
    Provider-->>Graph: reply
    Graph-->>RAG: reply and citation metadata
    RAG-->>Events: grounding check result
    Events-->>RunAPI: persisted reply, citations, trace
```

이 분리는 제품 설명에서 중요합니다. LangGraph는 AI 응답 흐름을 보여주고, RetrievalService/API 레이어는 실제 제품에 필요한 auth/permission/provenance 경계를 보여줍니다. Ingestion(upload/parse/chunk/embed)은 retrieval routing과 분리된 별도 pipeline입니다.

향후 retrieval 자체가 현재 RAG Agent contract graph를 넘어 query rewrite, metadata planning, hybrid/vector search, reranking, context compression 같은 graph/tool orchestration을 필요로 하면 ContextForge `RetrievalGraph`를 추가할 수 있습니다. 다만 그 경우에도 hard authorization filter는 graph prompt가 아니라 `RetrievalService` 안에 남겨야 합니다.

## OpenAI hosted tools를 추가할 위치

OpenAI Responses API의 `web_search` 같은 built-in tool은 **그래프 노드가 아니라 `responders.py`의 OpenAI provider 경계**에 추가하는 것이 가장 좋습니다.

이유:

- `graph.py`는 라우트 결정과 흐름 제어만 담당하게 유지할 수 있습니다.
- `respond_general`, `respond_research` 같은 노드는 provider 세부사항을 몰라도 됩니다.
- OpenAI 전용 기능은 `OpenAIResponseProvider` 안에 모아 provider 교체/테스트가 쉬워집니다.
- route-specific tool policy를 한 곳에서 테스트할 수 있습니다.

권장 구조:

```text
graph.py
  -> route 결정
  -> response node 선택
  -> route + guidance를 provider에 전달

responders.py
  -> route를 보고 사용할 OpenAI hosted tools 결정
  -> 필요하면 ChatOpenAI.bind_tools([...]) 적용
  -> 모델 호출
  -> reply 추출
  -> 나중에 citation/tool metadata 추출
```

## Web search policy 초안

현재는 아직 구현되지 않았습니다. 구현한다면 작은 단계로 시작합니다.

| 라우트 | web search 기본 정책 |
| --- | --- |
| `general_assistant` | 사용자가 최신/최근/출처/웹 검색을 명시하거나 현재 정보가 필요한 경우에만 허용 |
| `research_helper` | 기본 허용 |

첫 구현 milestone은 API 응답 스키마를 바꾸지 않고 provider 내부에서만 tool binding을 검증하는 것입니다. Citation과 tool metadata는 실제 응답 형태를 확인한 뒤 `ChatResponse`에 추가하는 것이 안전합니다.

## 변경 시 확인할 것

- 그래프 흐름을 바꾸면 `tests/test_graph.py`를 확인합니다.
- 라우팅 키워드를 바꾸면 `tests/test_classifier.py`와 대표 prompt fixture를 확인합니다.
- response provider 동작을 바꾸면 `tests/test_responders.py`를 확인합니다.
- OpenAI mode는 실제 API 키 없이 테스트 가능해야 합니다.
- README 변경 시 이 파일과 [`README.en.md`](./README.en.md)를 함께 갱신합니다.
