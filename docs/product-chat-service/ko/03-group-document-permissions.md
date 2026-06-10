# Group invitation과 document permission

[English original](../en/03-group-document-permissions.md) | 한국어

## 요약

Group/team은 초대를 수락한 뒤 참여하는 공유 지식 boundary입니다. Product client는 이메일 초대와 opaque invitation token 수락 흐름을 사용해야 하며, user search, account-existence 노출, 알려진 `user_id`로 직접 membership을 활성화하는 흐름을 제공하면 안 됩니다.

## 현재 계약

- 그룹은 공유 지식과 publish workflow의 경계입니다.
- Pending invitation은 active membership이 아니며 group KB 접근 권한을 주지 않습니다.
- Membership table은 수락된 멤버만 나타냅니다.
- Owner/admin은 초대 생성, 목록, 역할 수정, 재전송, 취소를 관리합니다.
- 초대받은 사용자는 로그인/가입 후 초대받은 이메일로 token을 수락해야 active member가 됩니다.
- Active member role update는 이미 수락된 멤버에게만 적용되며 새 멤버를 만들면 안 됩니다.
- Conversation transcript, run history, opt-in memory는 팀에 공유되지 않고 authenticated user 개인 범위로 유지됩니다.

## Permission 흐름

1. Document owner인지 확인합니다.
2. 명시적 user permission이 있는지 확인합니다.
3. 수락된 group membership role이 해당 작업을 허용하는지 확인합니다.
4. Pending invite, outsider, role mismatch는 deny-by-default입니다.

## 현재 제한

- public user search 또는 opt-in profile discovery는 아직 없습니다.
- organization/workspace identity management는 v1 group boundary 밖입니다.
- full audit log와 document-level deny override는 아직 없습니다.
- frontend 구현은 별도 `my-agents-frontend` repository에 있습니다.

## 테스트 근거

영어 원문은 invitation lifecycle, direct `user_id` activation 제거, pending invite 접근 차단, accepted member 권한, group document read/write, outsider denial, explicit document permission, non-manager denial을 검증 대상으로 둡니다. 이 한국어 문서는 같은 제품 의미를 유지하는 companion입니다.

## 변경 이력

- 2026-06-10: 초대 수락 기반 group/team membership boundary, privacy-preserving invitation 의미, 개인 conversation/memory 경계를 반영했습니다.
- 2026-05-17: 같은 주제의 한국어 문서 위치를 고정했습니다.
