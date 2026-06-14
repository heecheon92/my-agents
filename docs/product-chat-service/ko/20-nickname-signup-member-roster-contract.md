# Nickname signup과 member roster 계약

[English original](../en/20-nickname-signup-member-roster-contract.md) | 한국어

이 문서는 nickname signup/member-roster slice의 승인된 계약을 기록합니다. Backend migration, API schema, frontend schema, hosted OpenAPI handoff가 함께 반영되기 전까지는 planned contract입니다.

## 제품 경계

- `nickname`은 중복을 허용하는 표시 label입니다. 로그인 식별자, lookup key, profile discovery surface, uniqueness constraint가 아닙니다.
- Email은 signup/login과 invitation identifier로 유지합니다.
- `user_id`는 accepted member role maintenance를 위한 정확한 operational identifier로 유지합니다.
- Group/team membership은 계속 invitation acceptance 기반입니다. Public user search, account-existence 분기, 알려진 `user_id`로 직접 member activation, active roster의 member email 노출을 추가하면 안 됩니다.
- Pending invitation은 nickname이나 matched account metadata를 노출하면 안 됩니다. Active membership은 invitation acceptance 뒤에만 시작됩니다.

## Backend 계약

`POST /auth/signup`은 email/password와 함께 `nickname`을 요구해야 합니다.

```json
{
  "email": "person@example.com",
  "password": "correct horse battery staple",
  "nickname": "Heecheon"
}
```

검증 규칙:

- 저장 전 앞뒤 공백을 trim합니다.
- 누락되었거나 trim 후 빈 값이면 user 생성 전에 거절합니다.
- 1-40자 같은 bounded length를 둡니다.
- 서로 다른 user의 duplicate nickname은 허용합니다.
- identity 목적으로 case normalization이나 unique index를 추가하지 않습니다.
- Raw nickname을 log에 남기지 않습니다.

`UserResponse`는 signup, login, `/auth/me`, email verification, guest/session restore가 공유하는 safe serializer를 통해 `nickname`을 포함해야 합니다. 이 response field를 mandatory로 만들기 전에 기존 user와 guest row는 non-empty nickname으로 migration/backfill되어야 합니다.

## Manager-only member roster

`GET /groups/{group_id}/members`는 owner/admin 전용을 유지하고 accepted member의 display label을 반환해야 합니다.

```json
{
  "member_id": "membership-row-id",
  "user_id": "accepted-user-id",
  "nickname": "Heecheon",
  "role": "viewer",
  "created_at": "2026-06-14T..."
}
```

구현 guardrail:

- member nickname은 member마다 별도 query를 반복하지 않고 join/eager load 등으로 가져옵니다.
- `email`, profile data, account-existence flag, token, pending-invitation detail을 추가하지 않습니다.
- `PATCH /groups/{group_id}/members/{user_id}`는 non-creating이며 계속 `user_id` 기반입니다.
- `POST /groups/{group_id}/members`, nickname search, nickname 기반 role-update selector를 추가하지 않습니다.

## Frontend 계약

Frontend runtime schema는 backend-owned OpenAPI/contract가 새 field를 포함한 뒤에만 업데이트합니다.

- Signup UI는 required display name을 받고 email/password와 함께 `nickname`을 보냅니다.
- Auth copy는 display name이 group owner/admin의 member list에서 보이는 이름이며 login identity가 아니라고 설명합니다.
- Active member roster는 nickname을 primary human label로 보여주고 `user_id`는 secondary/advanced detail로 유지합니다.
- Duplicate nickname을 허용하므로 role update는 정확성을 위해 계속 user ID 기반입니다.
- Invitation UI는 email 기반과 non-enumerating copy를 유지합니다.

## 검증 체크리스트

Backend targeted check:

- missing/blank nickname은 user 생성 전에 실패합니다.
- valid nickname은 trim되어 `SignupResponse.user.nickname`으로 돌아옵니다.
- 서로 다른 email의 duplicate nickname signup은 성공합니다.
- signup/login/me/verify response는 nickname을 일관되게 포함합니다.
- existing-user migration/backfill은 non-empty nickname을 만듭니다.
- manager-only member list는 nickname을 포함하면서 email/profile/account-existence data를 계속 제외합니다.
- viewer/non-manager는 member directory를 조회할 수 없습니다.
- public OpenAPI는 `user_id` 직접 member 생성 route를 계속 노출하지 않습니다.

Frontend targeted check:

- backend contract가 요구할 때 auth Zod schema가 nickname을 요구합니다.
- signup form은 signup mode에서만 nickname을 렌더링하고 제출합니다.
- OpenAPI가 요구할 때 member schema가 nickname을 요구합니다.
- roster는 nickname을 보여주고 user ID는 secondary/advanced로 유지합니다.
- copy는 invitation privacy와 user-id role-update exactness를 유지합니다.
- e2e signup 및 group-member-management flow가 display-only boundary를 검증합니다.

## 변경 이력

- 2026-06-14: Cross-repo implementation handoff를 위한 planned nickname/display-only contract와 verification checklist를 생성했습니다.
