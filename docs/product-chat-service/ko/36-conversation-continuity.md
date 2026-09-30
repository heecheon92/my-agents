# 대화 맥락 유지와 임시 첨부 파일 재참조

[English](../en/36-conversation-continuity.md) | 한국어

## 상태와 맥락 정책

Product DB가 원문 대화, 메시지와 파일의 연결, run 근거, 재생성 가능한 대화 요약을 관리합니다.
Guest의 텍스트 대화도 요약할 수 있지만 요약은 해당 대화에만 속합니다. 기존 opt-in 장기 기억이나
동의 정책을 변경하지 않습니다. LangGraph checkpoint의 경계는 계속 `run_id`입니다.

최근 대화는 기본 16,000 추정 token과 보조 한도 64개 메시지 안에서 원문을 유지합니다. 조립한
**텍스트** 입력은 최대 32,000 추정 token입니다. 필요할 때 답변 전에 오래된 대화 구간을 요약하며
정리 후 최근 원문은 약 8,000 token을 목표로 합니다. 요약은 3,000 token, 관련 파일 메모는 총
4,000 token으로 제한합니다. 현재 질문과 필수 지침은 보호하며 너무 큰 질문은 run 접수 전에
거절합니다. 파일·이미지 자체의 처리 token은 별도 provider 한도 및 기존 파일 개수·크기 한도를
따릅니다. 텍스트 추정치를 실제 provider 입력 token 전체와 혼동하지 않습니다.

로컬에 있는 `o200k_base` cache를 사용하고 없으면 보수적인 UTF-8 byte 추정으로 전환합니다.
오프라인 실행 중 tokenizer를 내려받지 않습니다. Prompt의 추가 형식도 계산합니다. Run당 최대
두 번, 각각 30초 이내에서 요약합니다. 실패하면 유효한 기존 요약과 제한된 최근 원문으로 답변하되
누락된 맥락을 표시합니다. 빈 요약이나 추측을 새 기억으로 저장하지 않습니다. 취소한 결과는 설치하지
않습니다.

요약에는 포함한 원문 구간의 경계, digest, 정책 버전, 모델과 메시지 참조가 있습니다. Replay가 그
구간을 바꾸면 무효화합니다. 파일 삭제 또는 문서 권한 철회 시 관련 근거 내용을 이후 맥락에서
제외합니다. 과거 자료 준비, 현재 요청의 자료 전달, 답변의 정확성은 서로 다릅니다. 과거 전달 기록이
없으면 미확인 상태이며 현재 발췌문이 없다는 이유만으로 과거 답변을 근거 없는 답변이라고 단정하지
않습니다. 일반 로그에 원문 prompt를 남기지 않습니다. Jev의 human/assistant 메시지 4개, 각
4,000자 경계는 유지합니다.

## 모델과 공개 계약

`MY_AGENTS_SUMMARIZATION_MODEL` 기본값은 `gpt-6-luna`입니다. 일반 계정은 Settings에서
`GET/PATCH /summarization/preferences`로 변경할 수 있습니다. PATCH는
`{"summarization_model": 지원 모델 ID 또는 null}`이며 null은 배포 기본값을 따릅니다. 응답은
`customizable`, `default_model`, `selected_model`, `effective_model`입니다. Guest GET은 변경
불가 상태이고 PATCH는 403입니다. `/capabilities/summarization-models`는 노출 모델 3개를
`{id,name}`으로 제공하고 `recommended_model=gpt-6-luna`를 표시합니다. API는 지원 모델 6개를
모두 받습니다. Luna 사용을 강하게 권장하되 강제하지 않습니다. 요약은 답변의 reasoning 선택과
별도로 standard 및 수정 가능한 모델 기본 effort map을 사용합니다.

Run 접수 시 모델 선택을 고정합니다. 자동 파일 접근이 필요한지 아직 모를 때 started의 답변 모델은
미확인 상태일 수 있고 `run_model_resolved`가 실제 모델과 reasoning을 알립니다. 대화 정리는 검색과
답변 전에 `context_compaction_started/completed/failed`를 SSE 및 저장된 event로 제공합니다.
공개 payload에는 정책, 모델, 개수와 fallback 여부만 있으며 요약 본문은 없습니다. 종료 event의
개수는 정리한 메시지 수입니다. 취소한 run은 fallback 답변을 계속하지 않습니다.

선택적 UUID `client_request_id`가 요청, started event, run 목록을 연결합니다. 사용자별로
중복 사용하면 409이며 자동 재실행 기능은 아닙니다. 접수 확인 전 연결이 끊기면 정확한 ID로
확인하며 자동으로 다시 보내지 않습니다. ID가 없는 과거 run은 미확인 상태로 처리합니다.

## 파일과 보관 기간

원본 byte는 OpenAI Files에 저장합니다. 새 업로드의 기본 유효 기간은 업로드 후 7일이며 기존
파일의 만료 시각을 바꾸거나 조용히 연장하지 않습니다. Workspace는 20분 idle 수명을 유지하고
원본이 있으면 다시 생성할 수 있습니다. 제출하지 않은 업로드는 24시간 후 접근을 막고 정리합니다.
파일 메모는 정상 만료 후에도 대화에 남으며 대화 삭제 시 제거합니다. 메모는 provider가 보고한
관찰 내용과 검토 범위이고 전체 원문 복사본이나 독립 검증 결과가 아닙니다. 실패한 run의 메모는
후속 맥락에 공급하지 않습니다.

일반 계정은 해당 대화에 제출한 자신의 파일만 자동 재참조합니다. 현재 메시지에 명시적으로 첨부한
파일이 우선합니다. 이전 논의는 선택한 일반 assistant로 답변하고 정확한 확인·수정은 별도 workspace
모델로 원본을 다시 엽니다. 칭찬이나 무관한 질문에는 원본을 다시 열지 않습니다. 모호하면 V2
`attachment_selection`으로 제공한 ID 중에서 선택합니다. `access=notes`는 만료 후에도 저장된
논의를 사용할 수 있고 `access=original`은 원본이 있어야 합니다. 선택지에는 파일명, 유형, 원본
가용성과 선택적 크기·날짜가 있습니다. 정확한 작업에 원본이 필요하지만 만료되었으면 한계를 설명합니다.
Guest에게 workspace 파일 저장, 메모, 재참조를 허용하지 않습니다.

`MessageResponse.attachments`는 사용자가 그 메시지에 명시적으로 제출한 파일입니다. 과거 메시지,
첨부 없는 메시지, guest 메시지는 빈 목록입니다. Run의 첨부 목록에는 자동 재참조도 기록합니다.
Queue는 자신의 첨부 snapshot을 갖고 접수 확인 시 그 제출의 ID만 입력 영역에서 제거합니다.
접수 전 실패는 입력과 선택을 보존하고 접수 후 답변 실패는 자동 재전송하지 않습니다. Capability는
원본과 미제출 파일 TTL, 대화 단위 메모 보관, 자동 재참조 지원 여부를 제공합니다.

파일을 제거하면 즉시 앱 접근을 막고 메모를 지우며 관련 요약을 무효화합니다. 원본, container 복사본,
관련 결과물의 삭제 작업도 남깁니다. 기존 대화는 화면에 남지만 해당 파일에서 나온 답변은 이후 맥락에서
제외합니다. 대화 삭제는 원문과 파생 기록을 제거하기 전에 provider 정리 ID를 보존합니다. 실행 중인
run을 먼저 중지해야 삭제할 수 있습니다. 정리는 lease가 있는 outbox와 1분 간격 sweeper로 재시도하며
provider 장애 시 실제 삭제는 지연될 수 있습니다. Product DB에 업로드 원본 byte를 저장하지 않습니다.
Workspace 재사용 전에 선택하지 않은 입력 복사본을 제거하고 무관한 결과물의 기존 짧은 수명은 유지합니다.

## 적용과 검증

기존 DB는 실행 전에 Alembic `20260930_0036`을 적용합니다. Graph는
`general-assistant-checkpoint-v3`이므로 기존 V2 대기 run을 배포 전에 완료·취소해야 합니다.
호환되지 않는 checkpoint는 기존 안전한 실패 계약으로 처리합니다.
`MY_AGENTS_CONTEXT_CONTINUITY_ENABLED=false`는 제한된 기존 history와 명시적 첨부 방식으로
되돌리고 자동 재참조를 끕니다.

오프라인 검증은 맥락 예산, 요약 구간 재사용·실패, 모델 선호도, guest 경계, 접수 ID, 자동 파일 접근,
선택과 resume, 삭제 및 transaction 실패 복구를 확인합니다. 검증용 adapter와 실제 frontend proxy의
통합 검증은 계약과 흐름의 증거이며 모델 품질 검증이 아닙니다. 실제 요약 정확성·비용·지연은 대표적인
provider 평가가 필요합니다. 기존 SSE 실행은 여전히 요청 연결에 묶입니다. 결과물의 장기 보관과
첨부 없는 artifact 생성은 별도 과제입니다.

2026-09-30 구현 검증: backend 전체 786 passed / 13 skipped, 마지막 관련 검사 48 passed /
1 skipped, lint/format 통과. Frontend unit 425개와 최종 browser 229 passed / 2 skipped /
기존 실패 2개(전체 화면 file drop, transcript autoscroll)입니다. 실제 BFF/UI와 검증 adapter로
원본·메모 선택, 새로고침 복구와 resume를 확인했습니다. 실제 provider 품질이나 배포 완료의
증거는 아닙니다. 이후 소유자가 브라우저 수동 검증을 보고하고 commit/push를 승인했습니다.
[완료 기록](../../completed/conversation-continuity.md)에 범위와 검증을 보존합니다.
