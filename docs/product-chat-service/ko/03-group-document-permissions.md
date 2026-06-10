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

| Method | Path | Actor | Purpose | Privacy rule |
| --- | --- | --- | --- | --- |
| `POST` | `/groups/{group_id}/invitations` | owner/admin | 이메일과 role로 pending invitation 생성 | 계정 존재 여부를 드러내지 않는 동일한 응답 shape |
| `GET` | `/groups/{group_id}/invitations` | owner/admin | 관리 group의 pending/recent invitation 조회 | 관리자가 입력한 이메일은 보일 수 있지만 매칭 계정 정보는 노출하지 않음 |
| `PATCH` | `/groups/{group_id}/invitations/{invitation_id}` | owner/admin | pending invitation role 변경 | non-pending invitation은 거절 |
| `POST` | `/groups/{group_id}/invitations/{invitation_id}/resend` | owner/admin | invite token 재발급/재전송 | raw token과 account state를 노출하지 않음 |
| `DELETE` | `/groups/{group_id}/invitations/{invitation_id}` | owner/admin | pending invitation 취소 | 취소된 token은 수락 불가 |
| `GET` | `/groups/{group_id}/members` | accepted member | accepted member의 기본 role 정보 조회 | pending invite/account discovery field 없음 |
| `PATCH` | `/groups/{group_id}/members` | owner/admin | 이미 active 상태인 member role 수정 | non-creating; unknown/non-member user는 거절 |
| `POST` | `/group-invitations/accept` | authenticated recipient | opaque token 수락 | token을 인증된 verified email과 연결 |

`POST /groups/{group_id}/members`처럼 `user_id`로 active membership을 직접 만드는 product-facing route는 두지 않습니다.
`PATCH /groups/{group_id}/members`는 이미 active 상태인 member role만 수정할 수 있고 membership을 만들면 안 됩니다.
테스트나 seed가 직접 멤버십 setup이 필요하면 HTTP가 아닌 fixture/service helper를 사용합니다.

## 테스트 근거

영어 원문은 invitation lifecycle, direct `user_id` activation 제거, pending invite 접근 차단, accepted member 권한, group document read/write, outsider denial, explicit document permission, non-manager denial을 검증 대상으로 둡니다. 이 한국어 문서는 같은 제품 의미를 유지하는 companion입니다.

## 변경 이력

| Actor / scope | Read | Write | Manage permissions | Manage invitations | Ingest | Retrieve/cite |
| --- | --- | --- | --- | --- | --- | --- |
| Personal owner | Yes | Yes | Yes | N/A | Yes | Yes |
| Explicit viewer | Yes | No | No | No | No | Yes |
| Explicit editor | Yes | Yes | Optional grant | No | Optional grant | Yes |
| Group owner/admin | Yes | Yes | Yes | Yes | Yes | Yes |
| Group editor | Yes | Yes | No | No | Yes | Yes |
| Group viewer | Yes | No | No | No | No | Yes |
| Pending invitee | No | No | No | No | No | No |
| Unauthorized user | No | No | No | No | No | No |

## RAG와 privacy에 중요한 이유

Retrieval service는 전역 top-k를 먼저 가져온 뒤 나중에 필터링하지 않습니다. 권한이 있는 candidate set만
랭킹과 graph expansion에 들어갑니다. Invitation state도 같은 경계를 보호합니다. Pending invite는 retrieval
권한이 아니며, accepted group role은 승인된 group/document KB 접근만 허용합니다. Conversation과 user memory는
공유 지식 범위 밖에 유지됩니다.

## 현재 non-goals / 제한

- public user search 또는 discoverable profile directory 없음;
- organization/workspace identity management 없음;
- shared conversation transcript 또는 shared memory 없음;
- document-level deny override와 full audit log는 아직 없음;
- frontend invitation UI는 hosted OpenAPI의 최종 route/response shape에 맞춰야 함.

## 테스트 증거 방향

- owner/admin만 invitation 생성, 목록, 재전송, 취소, role 변경을 할 수 있어야 합니다.
- registered/unregistered email 초대 응답은 같은 public-safe shape이어야 합니다.
- pending invitation은 active membership을 만들지 않아야 합니다.
- 수락은 인증된 invited email에 묶이며 membership은 최대 하나만 생성되어야 합니다.
- 잘못된 사용자, 취소/만료/소비된 token, 중복 수락은 안전하게 실패해야 합니다.
- member list는 email/profile/account-existence field 없이 기본 member/role 정보만 보여야 합니다.
- owner/admin active-member role patch는 non-creating이어야 하며 missing/non-member user를 거절해야 합니다.
- public OpenAPI는 `user_id` 직접 membership 생성 route를 노출하지 않아야 합니다.
- 승인된 group document 읽기와 publish request workflow는 유지되어야 합니다.
- conversation transcript와 opt-in memory는 사용자별 private scope로 남아야 합니다.
