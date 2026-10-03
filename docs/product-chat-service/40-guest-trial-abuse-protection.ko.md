# 게스트 체험 자격과 남용 방지

[English](./40-guest-trial-abuse-protection.md) | 한국어

정규화된 이메일 하나당 **게스트 체험은 한 번**입니다. 운영자가 명시적으로 초기화한 경우만 예외입니다.
체험이 활성 상태일 때 새 코드를 사용하면 같은 계정으로 다시 로그인합니다. 만료 시각·할당량은 갱신하지 않고
계정 생성 알림도 다시 보내지 않습니다. 만료되거나 삭제된 체험은 공개 API로 다시 만들 수 없습니다.
승인 대기·거절을 포함해 일반 회원 계정이 있는 이메일은 일반 가입·인증·승인 경로를 사용해야 합니다.

## 식별과 동시성

`guest_trials`는 이메일을 `strip().casefold()`로 정규화한 HMAC을 primary key로 사용합니다.
기존 삭제 audit과 같은 `MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY`를 재사용합니다. 이름은 호환성을 위해
유지하지만 이제 **게스트 접근 또는 정리 기능 활성화 시** 최소 32바이트의 고정된 비밀 키가 필요합니다.
체험 ledger와 rate-limit bucket에는 원본 이메일을 넣지 않습니다. 발송과 코드 소유권은 요청 기록으로 확인합니다.
플러스 주소, Gmail의 점, 서로 다른 메일함은 같은 사람으로 합치지 않습니다.

고유 식별자 생성과 DB write/row lock으로 같은 이메일의 발급·사용·초기화를 직렬화합니다. 재발급은 기존 미사용
코드를 무효화하고 새 코드를 현재 체험 generation에 묶습니다. 코드 사용 시 자격을 다시 확인하고 코드 소비,
계정 생성·재사용, session 생성, 필요한 가입 알림 저장을 한 transaction으로 처리합니다. 실패하면 코드 소비도
rollback합니다. 같은 코드의 동시 사용은 계정을 둘 만들지 못하며, 같은 이메일의 서로 다른 유효 코드도 같은
계정을 사용합니다. 이메일 없는 운영자 코드로는 체험 계정을 만들 수 없습니다.

기존 게스트 session도 ledger에 선택된 계정인지 확인하여 과거 중복 계정이나 구버전 인스턴스가 뒤늦게 만든
계정의 우회를 막습니다. 게스트 기능이 켜져 있으면 일반·초대 가입도 같은 식별자 lock을 사용합니다.

## 요청 제한과 API

```dotenv
MY_AGENTS_GUEST_CODE_RESEND_COOLDOWN_SECONDS=60
MY_AGENTS_GUEST_CODE_EMAIL_DAILY_LIMIT=5
MY_AGENTS_GUEST_REQUEST_IP_HOURLY_LIMIT=30
MY_AGENTS_GUEST_LOGIN_IP_WINDOW_LIMIT=60
```

기본값은 이메일별 60초 재전송 대기, UTC 날짜당 코드 5개, 요청 origin별 시간당 30회, 로그인 origin별
15분당 60회입니다. 로그인 실패도 셉니다. 고정 시간창 counter는 Product DB에 공유되어 worker 변경이나
재시작으로 초기화되지 않습니다. 만료 bucket은 다음 검사 때 정리합니다. 시간창 경계를 걸친 요청은 두 창의
한도를 사용할 수 있으며 sliding-window 방식은 아닙니다.

Origin은 서버의 신뢰된 proxy 처리 이후 `request.client.host`입니다. 임의의 `X-Forwarded-For`를 직접 믿지
않습니다. BFF/proxy 뒤에서는 방문자가 같은 origin 한도를 공유할 수 있으므로 배포의 주소 전달을 따로 확인해야
합니다. 이를 브라우저·사람별 제한이라고 단정하지 마세요.

`POST /auth/guest/request`는 자격 충족, 이미 사용한 체험, 등록된 이메일, 이메일 제한 모두에 같은
`{"status":"accepted"}`를 반환합니다. 이 응답이 메일 발송을 보장하지는 않습니다. Origin 한도는 HTTP 429
(`rate_limit_exceeded`), 잘못되거나 만료·사용된 코드는 기존 HTTP 400, 제공자 실패는 기존 503을 유지합니다.
Local 모드는 오프라인 outbox를 사용합니다. 운영자 발급도 이메일 자격·재전송·일일 제한을 지키지만 네트워크
origin 제한은 적용하지 않습니다.

`GET /auth/guest/policy`에 `trial_policy=one_per_email`, `active_trial_relogin_supported=true`,
`code_resend_cooldown_seconds`, `code_email_daily_limit`을 추가합니다. 프론트엔드 안내는 한 번의 체험과
accepted 응답이 발송을 보장하지 않는다는 점을 설명해야 합니다. 별도 프론트엔드 코드는 이번 변경에 포함하지 않습니다.

## 기존 데이터와 배포

1. 기존 HMAC 키를 유지합니다. 정리 기능이 꺼져 있어도 게스트 활성화에는 이 키가 필요합니다.
2. 구버전 배포의 게스트 발급·로그인을 잠시 끄고, 배포 중 구버전·신버전 auth writer가 섞이지 않게 합니다.
3. `uv run alembic upgrade head`로 `20261003_0039`까지 적용합니다.
4. `uv run python -m scripts.guest_trial_policy backfill`로 초기화를 미리 봅니다. 식별자 건수만 출력하며
   `--apply` 없이는 변경을 rollback합니다.
5. `uv run python -m scripts.guest_trial_policy backfill --apply` 후 새 앱을 배포하고 게스트를 다시 켭니다.
   새 앱도 게스트 활성화 시 요청을 받기 전 멱등 초기화를 수행하며, service 호출에도 같은 보호 장치가 있습니다.

사용한 요청·코드, 연결된 계정, 삭제 audit을 모아 이메일별 체험 이력을 만듭니다. 활성 게스트가 여러 개이면
가장 먼저 생성된 계정(동률이면 ID 순)을 원래 만료·할당량 그대로 유지합니다. 나머지는 즉시 만료하고 session을
취소하되 데이터는 일반 정리 유예 기간까지 유지합니다. 일반 회원 이메일은 활성 게스트를 유지하지 않습니다.
중복 미사용 코드는 최신 유효 코드만 남깁니다. 이메일·체험 연결을 확인할 수 없는 계정은 게스트 session 인증을
통과하지 못하지만 초기화 과정에서 데이터를 지우지는 않습니다.

`guest_policy_state`의 초기화 표시와 키 검증값을 backfill과 함께 저장합니다. 이후 키 변경은 새 체험을
허용하는 대신 오류로 차단합니다. 최초 키는 과거 삭제 audit 생성에 사용한 키와 같아야 합니다. 지워진 원본
이메일로 fingerprint를 다시 계산할 수 없으며 자동 키 교체는 구현하지 않습니다. 앱 rollback 시 ledger를
지우거나 구버전 auth를 허용하지 말고 게스트 접근을 꺼 두세요.

## 운영자 초기화

```bash
uv run python -m scripts.guest_trial_policy reset --email guest@example.com
uv run python -m scripts.guest_trial_policy reset --email guest@example.com --apply
```

설정된 DB를 대상으로 하므로 production 선택은 운영자가 결정합니다. 공개 reset API는 없습니다.
초기화는 generation과 시각을 기록하고 이전 게스트·session·코드를 만료시킵니다. 다음 유효 코드 사용에 새
체험을 허용하지만 과거 데이터를 삭제하거나 일반 회원 차단·발송 한도를 우회하지는 않습니다.
명령 출력에는 입력 이메일이나 코드·자격 증명을 넣지 않습니다.

## 삭제와 검증 범위

계정 정리 후에도 체험의 사용 기록은 유지하고 삭제된 계정 포인터만 해제합니다. 같은 generation의 대기 코드도
정리하며 기존 최소 audit은 남깁니다. 정리·reset은 식별자 lock 이후 사용자 lock 순서를 지켜 새 체험 삭제를 막습니다.

오프라인 테스트는 순차·동시 발급과 사용, session 간 공유 한도, 갱신 없는 재로그인, 일반 회원 차단, reset,
rollback, 과거 중복 정리, 키 불일치, 계정 삭제 후 재가입 차단을 검증합니다. 동시성 테스트는 FK가 켜진 파일
SQLite와 독립 session을 사용합니다. PostgreSQL 배포 동시성과 proxy 주소 전달은 별도 검증이 필요합니다.
메모리 SQLite는 순차 smoke test용이며 production 게스트는 영구 저장소가 필요합니다.
같은 이메일의 할당량 갱신을 막는 정책이며, 여러 메일함이나 일반 회원 남용까지 해결하는 정책은 아닙니다.
