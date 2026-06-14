---
title: Nickname signup and member roster contract
updated: 2026-06-14
status: planned
locations:
  - my_agents/auth/schemas.py
  - my_agents/auth/models.py
  - my_agents/auth/service.py
  - my_agents/api/auth.py
  - my_agents/groups/schemas.py
  - my_agents/groups/service.py
  - my_agents/api/groups.py
topics:
  - auth
  - groups
  - privacy
  - frontend-contract
---

# Nickname signup and member roster contract

This note records the approved contract for the nickname signup/member-roster slice. It is a planned contract until the backend migration, API schemas, frontend schemas, and hosted OpenAPI handoff all land together.

## Product boundary

- `nickname` is a duplicate-allowed display label. It is not a login identifier, lookup key, profile-discovery surface, or uniqueness constraint.
- Email remains the signup/login and invitation identifier.
- `user_id` remains the exact operational identifier for accepted-member role maintenance.
- Group/team membership remains invite accepted: no public user search, no account-existence branching, no direct member activation by known `user_id`, and no member emails in the active roster response.
- Pending invitations must not expose nicknames or matched account metadata. Active membership starts only after invitation acceptance.

## Backend contract

### Signup and safe user responses

`POST /auth/signup` should require:

```json
{
  "email": "person@example.com",
  "password": "correct horse battery staple",
  "nickname": "Heecheon"
}
```

Validation rules:

- trim leading/trailing whitespace before persistence;
- reject missing or blank-after-trim values;
- keep a bounded length such as 1-40 characters;
- allow duplicate nicknames across different users;
- do not normalize case for identity and do not add a unique index;
- do not log raw nickname values.

`UserResponse` should include `nickname` through the shared safe serializer used by signup, login, `/auth/me`, email verification, and guest/session restore paths. Existing users and guest rows need a non-empty migration/backfill before this response field becomes mandatory.

### Manager-only member roster

`GET /groups/{group_id}/members` should remain owner/admin-only and return accepted members with display labels:

```json
{
  "member_id": "membership-row-id",
  "user_id": "accepted-user-id",
  "nickname": "Heecheon",
  "role": "viewer",
  "created_at": "2026-06-14T..."
}
```

Implementation guardrails:

- load member nicknames without N+1 per-member queries;
- do not add `email`, profile data, account-existence flags, tokens, or pending-invitation details;
- keep `PATCH /groups/{group_id}/members/{user_id}` non-creating and keyed by `user_id`;
- do not add `POST /groups/{group_id}/members`, nickname search, or nickname role-update selectors.

## Frontend contract

The frontend should update runtime schemas only after a backend-owned OpenAPI/contract includes the new fields.

- Signup UI collects a required display name and submits `nickname` with email/password.
- Auth copy should explain the display name is shown to group owners/admins in member lists and is not login identity.
- The active member roster shows nickname as the primary human label and keeps `user_id` as secondary/advanced detail.
- Role updates stay user-id based for exactness because duplicate nicknames are allowed.
- Invitation UI remains email-based and non-enumerating.

## Verification checklist

Backend targeted checks:

- missing nickname and blank nickname fail before user creation;
- valid nickname is trimmed and returned in `SignupResponse.user.nickname`;
- duplicate nicknames across different emails succeed;
- signup/login/me/verify responses include nickname consistently;
- existing-user migration/backfill creates non-empty nicknames;
- manager-only member list includes nickname and still omits email/profile/account-existence data;
- viewers/non-managers cannot list member directories;
- public OpenAPI still omits direct create-member-by-`user_id`.

Frontend targeted checks:

- auth Zod schemas require nickname where the backend contract requires it;
- signup form renders and submits nickname only in signup mode;
- member schema requires nickname once OpenAPI does;
- roster displays nickname while user ID remains secondary/advanced;
- copy keeps invitation privacy and user-id role-update exactness;
- e2e signup and group-member-management flows cover the display-only boundary.

## Revision history

- 2026-06-14: Created the planned nickname/display-only contract and verification checklist for the cross-repo implementation handoff.
