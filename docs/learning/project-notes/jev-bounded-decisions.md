---
created: 2026-09-23
updated: 2026-09-23
status: active
topics: [routing, jev, fallback, provider-boundary]
related_code:
  - my_agents/decisions.py
  - my_agents/agents/general_assistant/retrieval_gate.py
  - my_agents/agents/rag_agent/tool_selection.py
  - my_agents/agents/context_forge/planner.py
---

# Jev로 제한된 선택을 분리하기

기존 source gate는 답변용 모델을 호출해 두 label 중 하나를 골랐고, RAG selector는 Luna tool call을 사용했다.
이제 Jev는 미리 정한 choice를 선택하고, 기존 서비스가 권한 확인과 실행을 담당한다. 답변과 metadata 생성은 OpenAI에 남는다.
ContextForge는 intent만 Jev로 분류하며 structured entity 추출과 scope 결정은 기존 규칙을 유지한다.

```mermaid
flowchart LR
    Text["최근 대화: 최대 4개, 각각 4000자"] --> Jev["Jev choice API"]
    Jev --> Validate["응답 종류와 허용 label 검증"]
    Validate --> Decision["기존 decision 계약"]
    Jev -->|오류 또는 키 누락| Rules["로컬 규칙"]
    Rules --> Decision
    Decision --> Service["코드가 권한과 실행 범위를 강제"]
```

`ChatOpenAI` model 이름만 바꾸면 안 된다. Jev는 `/api/alpha/decisions`에 state와 questions를 보낸다.
`desk-lab`의 Choice 패턴을 참고하되 기존 httpx로 좁은 adapter를 구현해 dependency를 늘리지 않았다.
API 응답은 허용 label로 검증하며, 10초 timeout과 재시도 없는 호출 뒤 오류 시 로컬 규칙을 사용한다.
Confidence는 정답이나 권한의 증거가 아니므로 실제 데이터로 평가하기 전 임의 threshold는 두지 않는다.
Jev는 prose를 생성하지 않으므로 optional approach summary는 비워 둔다. 템플릿을 model-generated로 표시하면 기존 API 의미를 어기기 때문이다.

설정: `OPENROUTER_API_KEY`, `MY_AGENTS_DECISION_PROVIDER=jev`, `MY_AGENTS_JEV_TIMEOUT_SECONDS=10`.
`deterministic` response mode는 항상 외부 결정을 끈다. Decision provider를 `openai`로 바꾸면 이전 gate/selector와 로컬 intent로 되돌린다.
실제 한국어/영어 분류 정확도와 latency/cost는 아직 live 측정하지 않았다. Mock 테스트는 wire contract와 fallback을 검증할 뿐 정확도를 증명하지 않는다.

## Revision history

- 2026-09-23: 세 결정 지점의 Jev 경계, fallback, 검증 한계 정리.
