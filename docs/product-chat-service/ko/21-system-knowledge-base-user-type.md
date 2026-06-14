# System knowledge base와 user type 계약

[English original](../en/21-system-knowledge-base-user-type.md) | 한국어

이 slice는 project-level system knowledge를 추가하되, 이를 user memory로
취급하지 않습니다.

## 계약

- `users.user_type`은 `account_type`과 분리됩니다.
  - `account_type`은 계속 `registered` 또는 `guest`입니다.
  - `user_type`은 `normal`, `root`, `system`입니다.
- 이번 버전에서 `root`와 `system`은 같은 system-knowledge manager 권한입니다.
- User-type 변경은 operator script로만 수행합니다.

```bash
uv run python -m scripts.set_user_type --email owner@example.com --user-type root --dry-run
uv run python -m scripts.ops account set-user-type --email owner@example.com --user-type system
```

`user_type`을 지정하거나 변경하는 공개 API route는 없습니다.

## System knowledge 동작

- System KB는 `scope = "system"`, `group_id = null`, `purpose = "standard"`를 사용합니다.
- `owner_user_id`는 audit용 privileged creator를 기록합니다. 이 값이 public retrieval
  규칙을 의미하지는 않습니다.
- `/auth/me`는 `user_type`과 `can_manage_system_knowledge`를 노출합니다.
- Root/system 사용자는 system KB 생성, 목록/조회, 이름 변경, 삭제, 문서
  생성/upload/edit/ingest를 수행할 수 있습니다.
- Normal user와 guest는 system KB를 목록/관리할 수 없고, 추측한 system KB/document ID에는
  concealed not-found 응답을 받습니다.
- Authenticated chat retrieval은 guest를 포함한 모든 사용자에게 standard system KB를
  ambient context로 포함합니다.
- Public run metadata는 사용자가 선택/해결한 personal/group KB ID와 ambient system KB ID를
  분리합니다.

## 경계 메모

- System knowledge는 authenticated chat user에게 공개됩니다. secret을 업로드하지 마세요.
- System knowledge는 authorized retrieval context와 citation으로 나타나며, stored user
  memory가 아닙니다.
- Personal, group, publish-review, hidden staging, explicit document permission, guest-limit
  규칙은 별도 regression boundary로 유지합니다.
