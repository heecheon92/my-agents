---
created: 2026-09-30
updated: 2026-09-30
status: implemented
topics: [context-engineering, compaction, attachments, retention, streaming]
related_code:
  - my_agents/conversations/continuity.py
  - my_agents/conversations/file_context.py
  - my_agents/document_workspace/retention.py
  - my_agents/api/conversations/graph_streaming.py
---

# 대화 정리와 첨부 파일 기억을 분리하는 이유

메시지 원문은 Product DB의 기록이고 모델에 보내는 맥락은 그 기록에서 선택한 작업용 자료입니다.
둘을 같은 것으로 취급하면 짧은 history 한도가 실제 대화 삭제처럼 보입니다. 최근 원문과 오래된
구간의 요약을 조합하되 어떤 원문 구간을 요약했는지 경계를 남겨야 합니다. 사용자 수정이나 삭제로
그 구간이 바뀌면 다시 만들어야 합니다. 요약은 사용자 전체에 적용하는 장기 기억이 아닙니다.

파일에도 서로 다른 수명이 있습니다. 입력 영역의 선택은 다음 메시지에 보낼 파일을 뜻합니다.
접수 후 선택을 지워도 보낸 메시지와 원본 파일의 연결은 남습니다. 원본 byte는 OpenAI에 있고
대화에는 파일의 역할, provider가 보고한 관찰 내용, 검토 범위와 원문 참조만 남습니다. 원본이
만료되면 이전 논의를 기억할 수 있지만 정확한 수식이나 값을 다시 검증할 수는 없습니다.

자기 답변을 요약한다고 근거가 생기지 않습니다. “assistant가 이렇게 말했다”와 “원본에서 확인한
내용”을 구분해야 합니다. 과거 검색 결과가 현재 요청에 없다는 사실도 당시 검색하지 않았다는
증거가 아닙니다. 그래서 요약의 주장 종류, 실제 전달 metadata, 현재 권한을 별도로 관리합니다.

## 실패 경로에서 배운 점

Frontend와 실제 proxy로 통합 검사할 때 CSV run이 멈췄습니다. 원인은 CSV 자체가 아니라 검증용
adapter가 서로 다른 대화에도 같은 container ID를 사용한 것이었습니다. DB unique 제약으로 flush가
실패했고, 실패 처리 전에 ORM의 `run.id`를 읽다가 rollback이 필요한 session에서 다시 예외가 났습니다.
검증 adapter의 ID를 고유하게 바꾸고, 실행 전에 run ID를 문자열로 보존해서 실패 run과 terminal
SSE event를 기록하도록 고쳤습니다. 실제 DB 오류 경로도 회귀 검사합니다.

또 기존 streaming 경로는 interrupt 전에 RAG 결과가 있다고 가정했습니다. 새 첨부 선택은 RAG 전에
중단할 수 있으므로 정상적인 pause를 답변 누락 오류로 처리했습니다. 이 경우에도 semantic interrupt를
전달하고 선택 후 같은 checkpoint를 resume하도록 바꿨습니다. Sync 검사만으로는 찾지 못한 버그입니다.

삭제 정리는 DB metadata 상태와 provider의 실제 삭제가 다릅니다. API가 먼저 expired를 표시했어도
sweeper가 복사본을 정리해야 하며, 같은 정리 작업을 계속 생성하지 않도록 기록이 필요합니다. Outbox는
삭제한 대화의 provider ID를 잃지 않고 재시도하기 위한 장치입니다. Provider 장애 때 실제 삭제가 지연될
수 있으므로 앱에서의 접근 차단과 물리적 삭제를 구분합니다.

## 확인할 질문

- 원본이 없어도 답할 수 있는 회상인가, 정확한 원문이 필요한 작업인가?
- 요약이 포함한 구간과 최근 원문 사이에 설명하지 않은 누락이 있는가?
- 파일 제거 후 메모나 과거 assistant 답변이 다시 맥락에 들어오지는 않는가?
- 입력 token 절약뿐 아니라 사용자 수정 보존과 근거의 불확실성도 평가했는가?

현재 오프라인·검증 adapter 결과는 계약과 흐름의 증거입니다. 실제 모델의 요약 정확성, 비용과 지연은
별도로 확인해야 합니다. [현재 계약](../../product-chat-service/36-conversation-continuity.ko.md)을 참고합니다.

## Revision history

- 2026-09-30: 맥락·파일 수명 분리, transaction 실패 복구와 retrieval 전 interrupt 회귀를 기록했습니다.
