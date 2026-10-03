# 만료된 게스트 계정 정리

[English](./39-expired-guest-cleanup.md) | 한국어

게스트 만료 시 접근은 즉시 차단됩니다. 선택적으로 활성화하는 정리 기능은 `guest_expires_at` 이후 유예 기간
(기본 24시간)이 지나면 게스트의 비공개 데이터를 영구 삭제합니다. 프롬프트·업로드 한도 소진만으로 삭제하지
않으며 접근 만료 전까지 기존 답변을 읽을 수 있습니다. 일반 회원, 미만료 게스트, 유예 기간 내 게스트는 제외합니다.

## 활성화와 미리보기

`uv run alembic upgrade head`로 `20261003_0038` migration 적용 후 설정합니다.

```dotenv
MY_AGENTS_GUEST_CLEANUP_ENABLED=true
MY_AGENTS_GUEST_CLEANUP_GRACE_SECONDS=86400
MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY=<stable-random-secret-at-least-32-bytes>
```

HMAC 키는 본인 터미널에서 `python -c 'import secrets; print(secrets.token_hex(32))'` 등으로 생성해 배포 환경의
비밀 환경 변수로 보관합니다. 저장소에 넣지 마세요. 최소 32바이트의 같은 키를 재시작·인스턴스 간 유지해야
과거 이메일 fingerprint와 비교할 수 있습니다. 제공자 API 키와는 별개입니다. 기본값은 비활성화이며,
가입 알림 수신 주소나 현재 게스트 가입 활성화 여부와 독립적입니다. 활성화 시 파일 기반 SQLite 또는
PostgreSQL이 필요하고 메모리 SQLite는 거부합니다.

삭제 없이 설정된 DB의 대상과 보류 사유별 건수를 확인합니다.

```bash
uv run python -m scripts.cleanup_expired_guests
```

이메일·문서·자격 증명은 출력하지 않습니다. `--apply`는 실제 영구 삭제이며 정리 기능 활성화도 필요합니다.
앱 재시작 후 활성화된 FastAPI worker가 한 번에 최대 25개 게스트를 확인하고 60초 뒤 다시 처리합니다.
Keyset 페이지 이동으로 보류 계정 때문에 다른 계정의 정리가 막히지 않습니다. CLI는 전체 페이지를 순회합니다.

## 삭제 범위

- 게스트 사용자, session, 인증 token, 사용한 게스트 코드 및 다른 코드가 참조하지 않는 관련 요청.
- 비공개 개인 KB, 문서, 청크·임베딩, 파싱 결과, 메타데이터, 추출 실행, 구조화 사실, entity 연결.
  Entity 자체는 이번 삭제로 더 이상 참조되지 않을 때만 지웁니다.
- 대화, 메시지, 요약, run, event, citation, 메모리 설정·제안·원본·Store projection, 사용량 행.
  Checkpoint는 `run_id` 단위로 지웁니다.
- 게스트의 그룹 membership·문서 권한. 수락한 초대의 사용자 참조는 해제하고 그룹과 다른 회원은 유지합니다.
  게스트의 게시 요청 snapshot은 지우지만 다른 소유자의 게시된 사본은 유지합니다.

기존 가입 알림은 계정과 독립적으로 유지되어 전송할 수 있으며 이메일 주소는 전송 성공 시 지워집니다.
다른 코드가 참조하는 요청, 게스트가 단순히 접근했던 공유 자료는 보존합니다.

## 보류와 실패 처리

실행 중·취소 중 run, 대기·실행 중 ingestion, 그룹·시스템·공유 자료 소유, 타 사용자에게 권한을 준 개인 문서,
직접 게시된 KB, 타 사용자 run의 근거로 쓰인 문서가 있으면 계정 전체를 보류합니다. 소유권 불일치나 여러 요청
이메일도 확인이 필요합니다. 게스트가 생성한 그룹은 소유권을 먼저 정리해야 합니다. 자동 소유권 이전이나
실행 중 작업의 강제 취소는 구현하지 않습니다.

게스트는 보통 제공자 workspace를 사용할 수 없습니다. 과거·비정상 attachment/workspace 행이 있으면
운영자 확인을 위해 보류하며 외부 파일 삭제를 완료했다고 처리하지 않습니다. 원인을 해결하면 다음 scan에서
재시도합니다. 오래된 active 행은 무기한 보류 원인이 될 수 있습니다. 로그와 CLI에서 사유별 건수를 확인합니다.

계정별 write lock 안에서 자격을 다시 확인합니다. Framework API로 checkpoint·memory projection을 먼저 지운 뒤
Product DB transaction에서 콘텐츠 삭제와 audit 기록을 함께 저장합니다. 외부 삭제 실패 시 DB 참조를 유지해
재시도합니다. 외부 삭제 후 DB commit이 실패해도 만료 계정과 참조가 남아 같은 삭제를 반복할 수 있습니다.
여러 저장소를 아우르는 단일 transaction은 아닙니다. 종료 시 현재 계정만 마치고 다음 계정을 시작하지 않으며,
persistence 연결을 닫기 전에 worker가 끝나길 기다립니다. 오류 로그에는 payload 대신 예외 종류만 남깁니다.

## 남기는 기록과 재가입

`guest_deletion_audits`에는 삭제된 게스트 ID, 생성·만료·삭제 시각, 사유, 자원 건수, 정규화한 요청 이메일의
HMAC-SHA256 fingerprint를 보관합니다. 원본 이메일, 문서 이름·본문, 질문·답변, 자격 증명은 넣지 않습니다.
이메일 없는 운영자 코드는 fingerprint가 null입니다. 사용자 FK가 없어 계정 삭제 후에도 기록이 남습니다.

이는 향후 이메일별 체험 자격 정책을 위한 이력입니다. **반복 게스트 가입 제한은 아직 구현하지 않습니다.**
새 코드를 쓰면 새 계정이 생성되는 현재 동작은 유지됩니다. HMAC 이력은 익명화가 아닌 가명화이며 현재 자동
만료 정책은 없습니다. 기존 앱·인프라 로그와 백업은 이번 DB 정리 범위 밖입니다.

## 검증 범위

Foreign key를 켠 파일 SQLite로 비공개 데이터 삭제, 다른 사용자·일반 회원 보호, 유예 기간, dry run,
동시 정리, rollback·재시도, checkpoint 실패, 공유·고아 entity, memory projection, active·공유 자원 보류를
검증합니다. Migration/schema 확인 및 전체 offline 테스트가 필요합니다. 실제 PostgreSQL 동시성,
배포된 checkpoint 삭제와 scheduling은 별도 검증 대상이며 로컬 구현 중 production 삭제는 실행하지 않습니다.
