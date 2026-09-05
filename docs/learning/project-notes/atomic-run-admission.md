---
created: 2026-09-05
updated: 2026-09-05
status: implemented-unmerged
topics: [transactions, concurrency, sqlalchemy]
related_code: [my_agents/api/conversations/run_lifecycle.py, tests/test_run_admission.py]
---

# 대화 실행 시작의 경쟁 조건

두 요청이 모두 “진행 중인 답변 없음”을 읽은 뒤 각각 실행을 만들 수 있었습니다.
기존 테스트는 이미 실행 중인 행을 넣고 두 번째 요청을 보냈기 때문에 이 순서를 놓쳤습니다.

사전 조회만 강화하거나 프로세스 내부 lock을 쓰는 방식은 여러 worker에 걸친 경쟁을 막지
못합니다. 데이터베이스의 partial unique index가 최종 판정을 맡고, 새 메시지·실행·초기
이벤트를 하나의 transaction으로 저장합니다. 경쟁에서 진 요청은 rollback하므로 질문만
남는 현상도 방지합니다. SSE에서는 HTTP header를 보내기 전에 admission을 마칩니다.

별도 connection과 barrier로 두 사전 조회를 먼저 완료시킨 뒤 경쟁시켜 검증합니다.
PostgreSQL fixture는 search_path만 바꾸면 public의 기존 table을 발견해 생성을 생략할 수
있습니다. 실제 검증에서 이 문제를 발견했고 테스트가 만든 행만 정리했습니다. Fixture는
schema_translate_map으로 모든 table을 명시적으로 격리하고 finally에서 해제합니다.

## Revision history

- 2026-09-05: 경쟁 순서, 원자적 저장, 회귀 테스트와 PostgreSQL fixture 격리 교훈 기록.
