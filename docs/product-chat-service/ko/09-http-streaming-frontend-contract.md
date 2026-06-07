# HTTP streaming과 frontend contract

[English original](../en/09-http-streaming-frontend-contract.md) | 한국어

## 요약

이 문서는 `product-chat-service/09-http-streaming-frontend-contract.md`의 한국어 문서 트랙 항목입니다. 현재는 핵심 목적과 영어 원문 위치를 안내하는 요약본입니다.

현재 영어 원문은 일반 대화 실행의 `POST /conversations/{conversation_id}/runs/stream`뿐 아니라 답변 재생성용 `POST /conversations/{conversation_id}/messages/{message_id}/replay/stream`도 다룹니다. 재생성 스트림은 진행 이벤트와 `answer_delta`를 제공하고, 새 답변이 성공적으로 완료된 뒤에만 기존 답변 이후 transcript를 정리합니다. 실패 시 기존 답변은 보존됩니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/09-http-streaming-frontend-contract.md`에 있습니다.
- 이 한국어 파일은 같은 주제의 위치를 고정하기 위한 문서입니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/09-http-streaming-frontend-contract.md](../en/09-http-streaming-frontend-contract.md)
