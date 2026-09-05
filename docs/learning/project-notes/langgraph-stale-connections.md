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

초기 이벤트부터 실패 이벤트까지 약 47ms였으며, retrieval이나 답변 생성 이벤트는 없었습니다.
조사 시점에 진행 중인 run도 0개여서 unique index가 대화를 계속 점유한 상황은 아니었습니다.
수동으로 run 상태를 바꾸거나 DB row를 지우지 않았습니다.

기존 SQLAlchemy pool에는 `pool_pre_ping`이 있지만 새 LangGraph용 Psycopg pool에는
checkout check가 없었습니다. 서버가 idle connection을 끊어도 client의 `closed` 값은
다음 I/O 전까지 false일 수 있습니다. 연결을 빌려준 뒤에야 오류를 발견하면 그 요청은 실패합니다.
운영 연결을 끊은 정확한 원인은 확인하지 못했지만, 같은 취약 경로는 로컬에서 재현했습니다.

SQLAlchemy의 Product DB session과 LangGraph의 pool은 별개입니다. 따라서 일반 DB 작업이나
run 실패 상태 저장이 성공해도 checkpoint connection의 생존 여부까지 보장하지는 않습니다.

## 기존 배포 검증에서 놓친 부분

`/health`의 200과 인증 없는 interaction endpoint의 401은 서비스가 떠 있고 route가
존재한다는 검증입니다. Graph를 실행하거나 checkpoint를 읽지 않으므로 이 장애가 있어도
정상처럼 보입니다. 기존 restart/resume 테스트도 새 connection으로 상태 복구를 검증했을 뿐,
pool 안에서 서버가 끊어 버린 idle connection을 다시 빌리는 상황은 다루지 않았습니다.

## 수정과 검증

공유 pool에 `check=ConnectionPool.check_connection`을 지정했습니다. Psycopg가 연결을
전달하기 전에 확인하고, 실패하면 기존 pool acquisition timeout 안에서 새 연결을 구합니다.
Checkpoint와 Store 모두 같은 보호를 받으며 새 env나 DB migration은 없습니다.

회귀 테스트는 loopback PostgreSQL의 별도 임시 DB에서 테스트가 만든 idle connection의
backend PID만 종료합니다. Client가 아직 open으로 보는데도 기존 코드의 첫 read가 실패하는
것을 확인했습니다. 수정 후 checkpoint read와 Store read가 모두 성공했고 기존 restart/resume
테스트도 통과했습니다. 이 fault injection은 원격 DB에서 실행하지 않습니다.

수정 전에는 pool 설정 검증과 두 stale-connection read가 실패했고, 기존 restart 테스트는
통과했습니다. 수정 후 persistence 테스트 4개가 모두 통과했습니다. 전체 offline suite도
585 passed, 14 gated skips, 11 dependency warnings였고 Ruff lint/format을 통과했습니다.
로컬 재현은 `AdminShutdown` 또는 connection-lost 오류이며, 운영 proxy의 SSL 오류 문구까지
동일하게 재현한 것은 아닙니다.

## 배포와 운영 확인

긴급 복구를 위해 `e62d45a` (`Validate LangGraph pool connections before reuse`)를 `main`에
직접 commit/push한 뒤 같은 commit을 `develop`에도 fast-forward해 원격 반영을 확인했습니다.
기존 Alembic revision `0034`는 바뀌지 않았고 frontend 수정도 필요하지 않았습니다.

수정 배포 후 사용자가 실제 인증 chat 재시도에 대해 정상 동작한다고 보고했습니다.
이것은 **사용자가 확인한 즉시 chat 복구**이며, health/401 응답으로 추론한 결과가 아닙니다.
다만 긴 idle 뒤 재시도, 다른 기능 전체, 정확한 Render running-image SHA를 독립적으로
확인한 것은 아닙니다. 즉시 재시도는 기존 pool이 실패 뒤 connection을 교체한 경우에도
성공할 수 있으므로, stale 경로의 직접 근거는 로컬 fault-injection red/green 테스트입니다.

## 피한 수정과 남은 한계

TLS를 끄거나 migration을 재실행할 문제가 아닙니다. Pool lifetime만 줄여도 checkout 검사가
생기지는 않습니다. Graph 전체를 자동 재실행하면 model 호출·도구 작업이 중복될 수 있으므로
retry는 connection acquisition에만 맡깁니다. Check 이후 처리 도중의 단절과 실제 DB 장애는
여전히 실패할 수 있습니다. 운영 환경에서도 배포 후 idle 뒤 chat을 다시 검증해야 합니다.

Rollback도 이미지별로 판단해야 합니다. 현재 DB revision을 모르는 `13607ae` 이미지에서
자신의 Alembic script로 pre-deploy upgrade를 실행하면 revision을 찾지 못할 수 있습니다.
반면 직전 `7a450cc`는 이미 `0034`를 포함하므로 그 revision 불일치 문제는 없지만,
되돌리면 connection-pool 취약점도 돌아옵니다. 모든 rollback이 불가능하다고 단정하지 않고,
migration 호환성과 버그 재도입 위험을 구분합니다.

참고: [Psycopg connection quality](https://www.psycopg.org/psycopg3/docs/advanced/pool.html#connection-quality).

실제 Render pre-deploy 명령, 아직 적용하지 않은 강화안, 테스트 DB 격리와 운영 검증 절차는
[운영 runbook](../../product-chat-service/en/14-render-migration-and-rollback-notes.md#production-pre-deploy-guardrail)을
따릅니다. 사전 검증은 배포 후 connection의 생존을 보장하지 않으므로 runtime 보호를 대체하지 않습니다.

## Revision history

- 2026-09-05: 운영 증상, checkout check, 로컬 fault-injection 회귀 테스트와 복구 범위 기록.
- 2026-09-05: main/develop 반영, 사용자 확인 복구, 배포 검증의 사각지대와 rollback 범위 보완.
- 2026-09-05: 현재/제안 pre-deploy gate를 구분한 운영 runbook 연결.
