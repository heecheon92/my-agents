---
created: 2026-09-23
updated: 2026-09-30
status: active
topics: [model-migration, reasoning, compatibility]
related_code:
  - my_agents/model_defaults.py
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

## GPT-6.1 Sol과 모델별 기본 effort

GPT-6.1 Sol은 기존 `gpt-6-` prefix 조건에 잡히지 않아 Pro가 거부되고
`none`/`minimal`이 그대로 provider에 전달됐다. 이 모델을 명시적으로 인식하고
두 값 모두 `low`로 정규화한다. 공개 선택지는 그대로이며 저장과 실행 값이 일치한다.

Provider 문서의 기본값을 `model_defaults.py`에 모델별로 기록한다.
`gpt-5.6-luna`, `gpt-6-luna`, `gpt-6-sol`, `gpt-5.6-sol`, `gpt-6.1-sol`은
현재 모두 `medium`이다. Effort 환경 변수는 제거하고 실행 surface 모델을 먼저 선택한다.
일반 계정의 run별 선택과 replay 상속은 유지하며 guest는 모델 기본값을 사용한다.

광범위한 `gpt-6` prefix 확장은 미래 모델 호환성을 가정하므로 사용하지 않는다.
내부 RAG selector의 명시적인 standard/low 정책은 모델의 기본값과 구분해 유지한다.
기존 환경 변수는 무시하는 회귀 테스트를 두어 배포의 남은 값이 새 기본값을 덮지 못하게 한다.
전체 suite에서 OpenAI mode factory test의 dummy key 누락도 발견해 test fixture에
가짜 key를 넣었다. 실제 credential을 요구하거나 읽는 방식으로 해결하지 않는다.
기존 모델의 `none`은 그대로 유지한다. 실제 API 품질과 account access는 offline 검사로
확인할 수 없으며 별도 live 검증이 필요하다.
전체 offline suite는 713 passed / 13 skipped이며 Ruff lint/format도 통과했다.
Dependency deprecation warning 11개는 남아 있다.

## 사용자 모델 선택과 run 고정

사용자가 고른 모델은 Product DB의 user preference에 저장한다. 배포 기본값은 환경 변수로
유지하고 guest는 이를 그대로 사용한다. 모델별 기본 effort는 권장값으로 시작한 application
정책이며 변경할 수 있다. 선택 가능한 Astra의 `none`도 `low`로 변환한다.

실행 전에 실제 답변 모델을 run에 저장하고 runtime context로 provider factory에 전달한다.
Global Settings를 바꾸면 동시에 실행되는 다른 사용자의 모델이 섞일 수 있으므로 설정 copy와
모델별 provider cache를 사용한다. Identity prompt도 같은 copy의 모델 ID를 사용한다.
Preference 변경은 다음 run/replay부터 반영하며 HITL resume는 기존 run의 모델을 유지한다.
Document workspace는 사용자 확인에 따라 별도 모델을 유지한다.

기존 migration 테스트가 최신 ORM으로 과거 schema에 run을 insert하면 새 nullable column을
참조해 실패한다. Core insert로 실제 필요한 legacy column만 넣어 fixture를 수정한다.
새 migration 테스트의 Alembic `fileConfig`는 application logger를 전역 disable해 뒤의 logging
검사를 깨뜨렸다. Production logging을 변경하지 않고 test에서 `fileConfig`를 막아 격리한다.
SQLite upgrade/downgrade와 PostgreSQL offline SQL, 실제 선택/guest/reset/user isolation,
stream/replay/resume pinning 및 provider identity/cache 경계를 검증한다.

전체 계약과 최종 검증은 [모델 선택 계약](../../product-chat-service/35-assistant-model-preferences.md)과
[implementation tracking](../../implementation-tracking.md)이 소유한다.

## Supported와 exposed 목록 분리

`SUPPORTED_ASSISTANT_MODELS`는 API가 받을 수 있는 여섯 모델을 유지한다.
`EXPOSED_ASSISTANT_MODELS`는 공개 선택지 세 개(`gpt-6.1-sol`, `gpt-6-luna`, `gpt-6-astra`)만
관리하며 반드시 supported 목록의 subset이어야 한다. Module load에서 이 조건을 검증한다.
노출 목록으로 저장값을 검증하면 API로 선택한 기존 모델을 잘못 reset할 수 있으므로
저장된 preference 검증은 계속 supported 목록을 사용한다. UI는 노출하지 않는 현재 모델을
읽기 전용으로 보여주며 새 radio 선택지로 추가하지 않는다. 배포 기본값과 null reset도 그대로다.

## Revision history

- 2026-09-23: 모델 판정과 effective effort 정규화 경계 및 회귀 테스트 기록.
- 2026-09-27: 실제 GPT-5.6 API 거부 확인, minimal-to-low 확대 및 고정 공개 계약 기록.

- 2026-09-30: GPT-6.1 Sol 호환성 및 provider 권장 기본 effort map, 환경 변수 제거 기록.

- 2026-09-30: 사용자 모델 선택, run pinning과 migration/logging test 격리 설명 추가.

- 2026-09-30: 지원/노출 모델 목록과 subset 검증, 노출하지 않는 설정 보존 설명 추가.
