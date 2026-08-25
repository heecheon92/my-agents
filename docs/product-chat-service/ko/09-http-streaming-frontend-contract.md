# HTTP streaming과 frontend contract

[English original](../en/09-http-streaming-frontend-contract.md) | 한국어

## 요약

이 문서는 `product-chat-service/09-http-streaming-frontend-contract.md`의 한국어 문서 트랙 항목입니다. 현재는 핵심 목적과 영어 원문 위치를 안내하는 요약본입니다.

현재 영어 원문은 일반 대화 실행의 `POST /conversations/{conversation_id}/runs/stream`뿐 아니라 답변 재생성용 `POST /conversations/{conversation_id}/messages/{message_id}/replay/stream`도 다룹니다. 재생성 스트림은 진행 이벤트와 `answer_delta`를 제공하고, 새 답변이 성공적으로 완료된 뒤에만 기존 답변 이후 transcript를 정리합니다. 실패 시 기존 답변은 보존됩니다.

Opt-in 전체 문서 검토에서는 provider token을 즉시 노출하지 않고 최종 답변을 먼저
buffering합니다. `partial`이면 “전체 문서 검토가 아니며 0-N자만 읽었다”는 한국어/영어
안내를 backend가 먼저 붙인 다음 `answer_delta`로 보냅니다. 따라서 제한 안내가 답변
뒤늦게 나타나지 않습니다. 합친 delta는 `run_completed.reply`와 같습니다.

이 경로에서는 마지막 `answer_delta` 뒤, `answer_composed` 앞에 선택적으로
`full_document_read` event가 나타납니다. Event와
`run_completed.document_coverage`는 `complete|partial`, document metadata,
start/end offset, total chars를 공유하고 event에는 latency가 추가됩니다. Raw document
body와 내부 next cursor는 공개하지 않습니다.

전체 문서 답변을 replay할 때는 원래 event의 document를 그대로 preselect하고 현재
권한을 다시 검사합니다. 삭제되었거나 권한이 없으면 다른 document로 교체하지 않고
unavailable-source warning과 빈 coverage/citation으로 종료합니다.

완료 응답은 `consulted_sources`와 `citations`를 구분합니다. 전자는 모델에 제공된
user-visible source 전체 superset이고 후자는 최종 답변에서 근거가 확인된 보수적 subset입니다.
두 배열에 같은 source가 있으면 동일한 persisted `id`와 `chunk_id`를 사용합니다.
Attribution이 실행됐지만 source가 없으면 `consulted_sources: []`, attribution 도입 이전
legacy run이면 `consulted_sources: null`입니다. 이 필드는 sync 완료, 일반 SSE 및 resume SSE의
`run_completed`, replay 완료, `GET /conversations/{conversation_id}/runs/{run_id}`에서 동일하게
직렬화되어 새로고침 뒤에도 구분이 유지됩니다.

Backend attribution/audit 배열은 chunk 단위를 유지하지만 frontend citation presentation은
document 단위입니다. `document_id`로 grouping하고 `source_filename`이 있으면 우선 표시하며,
없으면 `document_title`을 사용합니다. `knowledge_base_name`과 deduplicated optional
`source_page`만 함께 보여주고 일반 citation 상세에서는 snippet과 document/KB/chunk ID를
숨깁니다.

2026-06-09 기준으로 중요한 알려진 한계도 추가되었습니다. 현재 streamed chat generation은 client-held HTTP/SSE request에 묶여 있어서, 사용자가 탭을 닫거나 다른 화면으로 이동해 stream이 끊기면 assistant message가 최종 저장되기 전에 run이 cancelled/failed로 terminalize될 수 있습니다. 이 경우 사용자가 conversation에 다시 들어와도 생성 중이던 답변이 보이지 않을 수 있습니다. 가까운 시일 내에 server-owned background run execution과 resumable/listener-style SSE 구조로 보완해야 합니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/09-http-streaming-frontend-contract.md`에 있습니다.
- 이 한국어 파일은 같은 주제의 위치를 고정하기 위한 문서입니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/09-http-streaming-frontend-contract.md](../en/09-http-streaming-frontend-contract.md)
