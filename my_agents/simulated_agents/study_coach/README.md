# Study Coach simulated agent

[English](./README.en.md)

이 폴더는 **Planner → Executor → Critic loop** 패턴을 연습하기 위한 에이전트 개발 랩입니다.

`graph.py`는 현재 실행 가능한 학습용 그래프입니다. 구현 목적은 프로덕션 품질보다 요구사항을 LangGraph 노드, 상태, 라우팅, 종료 조건으로 직접 번역하는 연습입니다.

## 연습할 패턴

```text
User
  ↓
Planner
  ↓
Executor
  ↓
Critic
  ├── approved → END
  └── revise → Executor
```

이 패턴의 핵심은 한 에이전트가 바로 답하지 않고, 역할을 나누어 답변 품질을 개선하는 것입니다.

- **Planner**: 무엇을 배울지와 어떤 순서로 설명할지 계획합니다.
- **Executor**: 계획을 바탕으로 실제 설명을 작성합니다.
- **Critic**: 설명이 충분한지 평가하고, 부족하면 수정 지시를 냅니다.
- **Route function**: Critic 결과와 수정 횟수를 보고 종료할지 다시 Executor로 보낼지 결정합니다.

## 에이전트 목표

사용자가 어려운 개발 주제를 입력하면, Study Coach는 작은 학습 계획을 만들고 설명한 뒤, 그 설명이 초보자에게 충분히 좋은지 검토해야 합니다.

예시 입력:

```text
I want to understand LangGraph conditional edges.
```

## 요구 동작

### 1. Planner node

Planner는 사용자에게 직접 최종 답변하지 않습니다.

Planner는 `Plan` structured output을 생성하고 상태의 `plan`에 저장합니다.

```python
{
    "topic": "LangGraph conditional edges",
    "learning_goal": "Understand how routing decisions move graph execution",
    "steps": [
        "Explain what conditional edges are",
        "Show a tiny example",
        "Give one practice question",
    ],
}
```

Planner 책임:

- 사용자가 배우고 싶은 주제를 찾습니다.
- 학습 목표를 짧게 정합니다.
- 설명 단계를 만듭니다.
- 다음 노드인 Executor에게 계획을 넘깁니다.

### 2. Executor node

Executor는 Planner가 만든 계획을 바탕으로 사용자를 가르칩니다.

Executor 출력에는 다음이 있어야 합니다.

- 쉬운 설명
- 예시
- 연습 문제 또는 다음 학습 행동

Critic이 이전 답변을 거절했다면 `critic_decision.revision_instruction`을 참고해서 답변을 수정합니다.

### 3. Critic node

Critic은 Executor의 답변을 검토합니다.

Critic은 `DraftReview` structured output을 생성하고 상태의 `critic_decision`에 저장합니다.

```python
{
    "approved": True,
    "reason": "The answer includes a simple explanation, an example, and one exercise.",
    "revision_instruction": "",
}
```

또는 수정이 필요하면:

```python
{
    "approved": False,
    "reason": "The answer is too abstract for a beginner.",
    "revision_instruction": "Rewrite with a smaller concrete LangGraph example.",
}
```

Critic 승인 기준:

- 쉬운 설명이 있는가?
- 구체적인 예시가 있는가?
- 연습 문제나 다음 행동이 있는가?
- 지나치게 추상적인 표현을 피했는가?

## 반복 규칙

Critic이 승인하면 종료합니다.

Critic이 거절하면 Executor로 돌아가 수정합니다.

무한 루프를 막기 위해 최대 수정 횟수는 2번입니다.

```python
if verdict.approved:
    return "END"

if revision_count >= 2:
    return "END"

return "executor"
```

## 상태 설계

현재 구현은 그래프 전체의 공유 상태를 `StudyCoachState`로 이름 붙입니다.

```python
class StudyCoachState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    topic: str
    plan: Plan
    draft_answer: str
    critic_decision: DraftReview
    revision_count: int
```

이름이 `AgentState`가 아닌 이유는 상태가 하나의 agent/node 소유가 아니라 전체 Study Coach 그래프의 공유 노트북이기 때문입니다.

## 실행 방법

OpenAI API 키가 필요합니다.

```bash
uv run python -m my_agents.simulated_agents.study_coach.graph
```

종료:

```text
/exit
```

실행 중에는 학습을 위해 다음 debug 로그를 출력합니다.

```text
[planner] creating learning plan
[executor] writing draft answer
[critic] reviewing draft answer
[route] deciding next node
[final answer]
```

## 학습 포인트

이 그래프는 MBTI swarm 예제와 다른 패턴을 연습합니다.

- MBTI 예제: 에이전트 간 handoff / swarm 패턴
- Study Coach 예제: 생성 → 평가 → 수정 루프 패턴

이 패턴은 실제 에이전트 시스템에서도 자주 쓰입니다.

- 코드 작성자 → 코드 리뷰어
- 답변 생성기 → 평가자
- 연구자 → 사실 검증자
- 계획자 → 실행자 → 검증자

## 구현 제약

- 가능한 한 inline 코드로 구현합니다.
- 재사용 가능한 wrapper 함수보다 LangGraph primitive 이해를 우선합니다.
- 프로덕션 API/CLI surface로 연결하지 않습니다.
- debug print는 학습을 위해 의도적으로 남겨둡니다.
