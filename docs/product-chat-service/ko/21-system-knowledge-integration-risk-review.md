# System knowledge 통합 리스크 리뷰

[English original](../en/21-system-knowledge-integration-risk-review.md) | 한국어

이 체크리스트는 system knowledge base와 `user_type` rollout을 지원합니다. Backend,
frontend, verification lane의 handoff artifact이며, consensus plan 밖의 새 product
scope를 만들지 않습니다.

## 유지해야 할 계약 경계

- **System knowledge는 ambient internal retrieval context입니다.** Registered user와
  guest의 답변에 영향을 줄 수 있지만 user memory도, user-visible source class도 아닙니다.
- **관리 화면 가시성과 채팅 retrieval은 분리됩니다.** Normal user와 guest는 채팅에서
  system context를 받을 수 있지만 source-management surface에서 system KB 이름이나 ID를
  enumerate하면 안 됩니다.
- **`user_type` 변경은 운영자 script 전용입니다.** Public API request, profile update,
  frontend form이 `user_type`을 받거나 저장하면 안 됩니다.
- **v1에서는 `root`와 `system`이 동등합니다.** Frontend policy는 raw enum check보다
  derived capability인 `can_manage_system_knowledge`를 우선 사용해야 합니다. 이
  capability와 raw type은 모든 사용자에게 `false`/`normal` metadata로 보내지 않고
  root/system user에게만 보냅니다.
- **개인/group 경계는 그대로 유지됩니다.** Owner scoping, accepted-member group scoping,
  published personal KB 동작, document permission, guest limit을 약화하면 안 됩니다.

## 통합 리스크 체크리스트

최종 통합 sign-off 전에 확인합니다.

1. **Public metadata non-enumeration**
   - Normal/guest `/knowledge-bases` 응답에서 system row가 제외됩니다.
   - Normal/guest가 system KB/document ID를 추측해 직접 접근할 때 주변 기존 route와 같은
     concealed unauthorized style을 사용합니다.
   - Conversation/run/event public metadata와 citation은 ambient system KB 이름, ID, count,
     document/chunk ID, filename, snippet을 노출하지 않습니다.
2. **Migration and identity defaults**
   - `users.user_type`은 non-null이고 `normal`로 default/backfill되며 `account_type`, guest
     expiry, approval field를 변경하지 않습니다.
   - Signup, guest creation, local demo seed data, test fixture는 test가 명시적으로 `root`
     또는 `system`을 설정하지 않는 한 non-privileged user를 만듭니다.
   - Auth user response는 derived capability인 `can_manage_system_knowledge`가 `true`일
     때만 노출하고, `user_type`을 받는 public route를 추가하지 않습니다.
3. **Filter split and direct document-route scope awareness**
   - Management-visible KB filter와 chat-retrievable KB filter는 이름을 분리합니다.
   - System KB 포함은 KB predicate와 document readability predicate 양쪽에 구현합니다.
     한쪽만 바꾸는 것은 incomplete입니다.
   - Nested system document route는 root/system 전용입니다.
   - Direct `/documents/{id}` read/edit/delete/ingest 동작은 parent KB scope를 인식하고
     system document를 일반 owner-personal document처럼 취급하지 않습니다.
   - Global document list는 normal/guest 사용자에게 개인/group 중심 동작을 유지합니다.
4. **Guest promotion refusal**
   - Operator script는 기본적으로 guest account를 `root` 또는 `system`으로 승격하지 않습니다.
   - Guest `/auth/me`는 non-privileged 상태를 유지하면서도 채팅의 ambient system retrieval은
     허용합니다.
5. **Personal/group regression preservation**
   - Personal KB owner-only create/list/document-write 동작이 계속 통과합니다.
   - Group KB visibility와 publish-review flow는 membership/invitation scope를 유지합니다.
   - Hidden team-upload-staging KB는 일반 list와 retrieval surface에서 계속 제외됩니다.
6. **Evidence/source boundary**
   - System KB snippet은 model과 internal audit record에는 유지하지만 user-facing citation과
     source metadata에서는 제거합니다.
   - Personal/group citation은 기존 provenance와 함께 계속 표시합니다.
   - General assistant docs와 prompt는 memory, document retrieval, conflict를 분리된 internal
     source channel로 유지합니다.

## 권장 verification bundle

Lane 통합 후 backend gate를 실행합니다.

```bash
uv run pytest -q
uv run ruff check . --no-cache
uv run ruff format --check .
git diff --check
```

Sibling frontend repo 변경이 통합되면, 해당 repo에서 현재 `package.json` command name에
맞춘 frontend gate도 실행합니다.
