---
created: 2026-09-05
updated: 2026-09-05
status: implemented
topics: [langgraph, psycopg, connection-pooling, production-debugging]
related_code: [my_agents/persistence/langgraph.py, my_agents/persistence/database.py, tests/test_langgraph_persistence.py]
---

# LangGraph의 끊어진 idle connection 복구

## 증상과 원인

배포 뒤 실제 chat이 최초 `PostgresSaver.get_tuple()`에서 SSL connection 종료 오류로
실패했습니다. SSE의 HTTP 200은 stream 시작만 뜻하며 답변 완료를 보장하지 않습니다.
해당 run은 `failed`로 저장됐습니다. Migration과 framework table은 이미 준비되어 있었습니다.

기존 SQLAlchemy pool에는 `pool_pre_ping`이 있지만 새 LangGraph용 Psycopg pool에는
checkout check가 없었습니다. 서버가 idle connection을 끊어도 client의 `closed` 값은
다음 I/O 전까지 false일 수 있습니다. 연결을 빌려준 뒤에야 오류를 발견하면 그 요청은 실패합니다.
운영 연결을 끊은 정확한 원인은 확인하지 못했지만, 같은 취약 경로는 로컬에서 재현했습니다.

## 수정과 검증

공유 pool에 `check=ConnectionPool.check_connection`을 지정했습니다. Psycopg가 연결을
전달하기 전에 확인하고, 실패하면 기존 pool acquisition timeout 안에서 새 연결을 구합니다.
Checkpoint와 Store 모두 같은 보호를 받으며 새 env나 DB migration은 없습니다.

회귀 테스트는 loopback PostgreSQL의 별도 임시 DB에서 테스트가 만든 idle connection의
backend PID만 종료합니다. Client가 아직 open으로 보는데도 기존 코드의 첫 read가 실패하는
것을 확인했습니다. 수정 후 checkpoint read와 Store read가 모두 성공했고 기존 restart/resume
테스트도 통과했습니다. 이 fault injection은 원격 DB에서 실행하지 않습니다.

## 피한 수정과 남은 한계

TLS를 끄거나 migration을 재실행할 문제가 아닙니다. Pool lifetime만 줄여도 checkout 검사가
생기지는 않습니다. Graph 전체를 자동 재실행하면 model 호출·도구 작업이 중복될 수 있으므로
retry는 connection acquisition에만 맡깁니다. Check 이후 처리 도중의 단절과 실제 DB 장애는
여전히 실패할 수 있습니다. 운영 환경에서도 배포 후 idle 뒤 chat을 다시 검증해야 합니다.

참고: [Psycopg connection quality](https://www.psycopg.org/psycopg3/docs/advanced/pool.html#connection-quality).

## Revision history

- 2026-09-05: 운영 증상, checkout check, 로컬 fault-injection 회귀 테스트와 복구 범위 기록.
