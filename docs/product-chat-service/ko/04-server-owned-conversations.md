# 서버 소유 conversation과 chat run

[English original](../en/04-server-owned-conversations.md) | 한국어

## 요약

대화 transcript는 사용자 소유의 비공개 범위로 저장됩니다. 그룹 멤버십은 다른 사용자의 대화를 읽을 권한을 주지 않으며, 그룹 지식은 대화 범위가 아니라 `knowledge_base_selection`으로 선택되는 검색 출처입니다.

명시적인 문서 전체 검토 요청이 full-document 경로를 사용하면 완료된 run은
`document_coverage`에 `complete` 또는 `partial` 범위 metadata를 저장합니다. 정규화된
문서 본문은 답변을 만드는 동안에만 읽고 Product DB event나 LangGraph checkpoint에는
복사하지 않습니다. Run detail을 다시 읽어도 coverage를 복원할 수 있습니다.

Replay는 원래 `full_document_read` event가 가리키는 document를 그대로 다시 사용합니다.
그 문서가 삭제되었거나 현재 권한이 없으면 다른 document를 자동으로 대신 고르지 않고
unavailable-source warning을 반환합니다. 큰 문서는 현재 첫 번째 설정 범위만 검토하며
자동 multi-range 순회와 전체 synthesis는 아직 구현되지 않았습니다.

## 문서 상태

- 영어 원문은 `docs/product-chat-service/en/04-server-owned-conversations.md`에 있습니다.
- 2026-06-07 기준 deprecated group-conversation scope는 제거되었습니다.
- 2026-08-24 기준 explicit-intent full-document coverage와 원본 target을 유지하는 replay가 구현되었습니다.
- 상세 번역이 필요하면 이 파일을 확장하고, 영어 원문과 의미가 어긋나지 않게 유지하세요.

## 관련 위치

- 영어 원문: [product-chat-service/04-server-owned-conversations.md](../en/04-server-owned-conversations.md)
