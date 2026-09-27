---
created: 2026-09-23
updated: 2026-09-27
status: active
topics: [model-migration, reasoning, compatibility]
related_code:
  - my_agents/reasoning.py
  - my_agents/api/reasoning.py
  - tests/test_reasoning_compatibility.py
---

# GPT-6 reasoning 호환성

증상: 모델 설정만 GPT-6로 바꾸면 Pro 요청이 API에서 400으로 거부되고,
`minimal`은 provider까지 그대로 전달된다. 모델 이름 판정이 GPT-5.6에 고정된 것이 원인이다.

GPT-5.6/GPT-6의 Pro를 허용하고 두 계열 모두 `minimal`을 `low`로 바꾼다.
모델 기본값과 공개 effort 목록은 유지한다. GPT-6 Astra의 `none` 처리 변경은 이번 범위가 아니다.

Provider에서만 변환하면 저장된 run에는 minimal, 실제 실행에는 low가 남아 서로 달라진다.
따라서 API에서 대상 surface의 모델을 선택한 뒤 정규화하고, 실제 값을 저장한다.
공통 provider helper에서도 같은 변환을 적용해 CLI와 OpenAI fallback 경로를 보호한다.
Guest는 사용자 선택을 무시하고 서버 기본값을 고른 다음 같은 정규화를 적용한다.
Replay의 과거 minimal 값에도 적용하되 과거 run 자체는 수정하지 않는다.

테스트는 chat/workspace, request/default/guest/replay, capability, provider payload와
run/event 저장값을 credential 없이 검증한다. Live GPT-6 API 호출은 별도 검증이다.

## GPT-5.6 rollback에서 확인한 누락

2026-09-27 정규화 없는 직접 API smoke에서 GPT-5.6 Sol의 `none`은 성공했고
`minimal`은 HTTP 400 `unsupported_value`로 거부됐다. 기존에 GPT-5.6 minimal을 그대로
통과시키던 테스트는 provider 호환성을 검증하지 못했다. 해당 기대값을 고치고
GPT-5.6에도 같은 정규화를 적용했다. API 응답 본문이나 계정 식별자는 기록하지 않는다.

공통 SDK enum 전체를 특정 모델의 지원 목록으로 간주하는 접근은 폐기한다.
사용자 선택지 `none, minimal, low, medium, high, xhigh, max`는 고정하며,
향후 모델별 차이는 normalizer에서 처리한다. 알 수 없는 모델은 별도 지원값 검증이 필요하다.

## Revision history

- 2026-09-23: 모델 판정과 effective effort 정규화 경계 및 회귀 테스트 기록.
- 2026-09-27: 실제 GPT-5.6 API 거부 확인, minimal-to-low 확대 및 고정 공개 계약 기록.
