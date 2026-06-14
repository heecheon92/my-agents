"""Group invitation boundary API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.auth.models import UserModel
from my_agents.groups.models import GroupInvitationModel, MembershipModel
from my_agents.persistence.database import get_database_session

from .conftest import latest_auth_email_token, verify_latest_auth_email


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(create_app())


def _signup_login(client: TestClient, email: str, *, nickname: str = "Test User") -> str:
    password = "correct horse battery staple"
    signup = client.post(
        "/auth/signup",
        json={"email": email, "nickname": nickname, "password": password},
    )
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _create_group(owner: TestClient, *, name: str = "Invite Group") -> str:
    response = owner.post("/groups", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _user_by_email(email: str) -> UserModel | None:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return db.scalar(select(UserModel).where(UserModel.email == email))
    finally:
        session_generator.close()


def _invitation_rows(group_id: str) -> list[GroupInvitationModel]:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return list(
            db.scalars(
                select(GroupInvitationModel).where(GroupInvitationModel.group_id == group_id)
            ).all()
        )
    finally:
        session_generator.close()


def _membership_row(group_id: str, user_id: str) -> MembershipModel | None:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == group_id,
                MembershipModel.user_id == user_id,
            )
        )
    finally:
        session_generator.close()


def test_owner_invites_and_recipient_accepts_without_token_or_profile_leak(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    recipient = _client(monkeypatch)
    _signup_login(owner, "invite-owner@example.com", nickname="Invite Owner")
    recipient_id = _signup_login(
        recipient,
        "invite-recipient@example.com",
        nickname="Invite Recipient",
    )
    group_id = _create_group(owner)

    created = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "Invite-Recipient@Example.com", "role": "viewer"},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["group_id"] == group_id
    assert payload["invited_email"] == "invite-recipient@example.com"
    assert payload["role"] == "viewer"
    assert payload["status"] == "pending"
    assert "token" not in payload
    assert "user_id" not in payload
    assert "account_exists" not in payload
    assert recipient.get(f"/groups/{group_id}").status_code == 404
    assert _membership_row(group_id, recipient_id) is None
    invitation = _invitation_rows(group_id)[0]
    raw_token = latest_auth_email_token("invite-recipient@example.com", "group_invitation")
    assert invitation.token_hash != raw_token

    accepted = recipient.post("/group-invitations/accept", json={"token": raw_token})

    assert accepted.status_code == 200
    accepted_payload = accepted.json()
    assert accepted_payload["user_id"] == recipient_id
    assert accepted_payload["nickname"] == "Invite Recipient"
    assert accepted_payload["role"] == "viewer"
    assert recipient.get(f"/groups/{group_id}").status_code == 200
    members = recipient.get(f"/groups/{group_id}/members")
    assert members.status_code == 403
    manager_members = owner.get(f"/groups/{group_id}/members")
    assert manager_members.status_code == 200
    assert {member["user_id"] for member in manager_members.json()} >= {recipient_id}
    assert any(member["nickname"] == "Invite Recipient" for member in manager_members.json())
    assert all(
        "email" not in member and "profile" not in member for member in manager_members.json()
    )


def test_invited_new_user_signs_up_with_nickname_and_password_only(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    invitee = _client(monkeypatch)
    _signup_login(owner, "nickname-invite-owner@example.com", nickname="Invite Owner")
    group_id = _create_group(owner, name="Nickname Invite Group")

    created = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "New-Invitee@Example.com", "role": "editor"},
    )
    assert created.status_code == 201
    token = latest_auth_email_token("new-invitee@example.com", "group_invitation")

    signup = invitee.post(
        "/group-invitations/signup",
        json={
            "token": token,
            "nickname": "  Mom Display  ",
            "password": "correct horse battery staple",
        },
    )

    assert signup.status_code == 201
    assert "my_agents_session=" in signup.headers["set-cookie"].lower()
    payload = signup.json()
    assert payload["csrf_token"]
    assert payload["user"]["email"] == "new-invitee@example.com"
    assert payload["user"]["nickname"] == "Mom Display"
    assert payload["user"]["email_verified_at"] is not None
    assert payload["user"]["approval_status"] == "approved"
    assert "user_type" not in payload["user"]
    assert "can_manage_system_knowledge" not in payload["user"]
    assert payload["member"]["role"] == "editor"
    assert payload["member"]["nickname"] == "Mom Display"

    user = _user_by_email("new-invitee@example.com")
    assert user is not None
    assert _membership_row(group_id, user.id) is not None
    assert invitee.get(f"/groups/{group_id}").status_code == 200

    nickname_login = _client(monkeypatch).post(
        "/auth/login",
        json={"email": "Mom Display", "password": "correct horse battery staple"},
    )
    email_login = _client(monkeypatch).post(
        "/auth/login",
        json={"email": "new-invitee@example.com", "password": "correct horse battery staple"},
    )

    assert nickname_login.status_code == 422
    assert email_login.status_code == 200


def test_invitation_signup_does_not_replace_existing_account(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    existing = _client(monkeypatch)
    _signup_login(owner, "existing-invite-owner@example.com")
    existing_id = _signup_login(existing, "existing-invitee@example.com")
    group_id = _create_group(owner, name="Existing Invite Group")
    created = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "existing-invitee@example.com", "role": "viewer"},
    )
    assert created.status_code == 201
    token = latest_auth_email_token("existing-invitee@example.com", "group_invitation")

    signup = _client(monkeypatch).post(
        "/group-invitations/signup",
        json={
            "token": token,
            "nickname": "Duplicate",
            "password": "correct horse battery staple",
        },
    )

    assert signup.status_code == 409
    assert _membership_row(group_id, existing_id) is None


def test_viewer_cannot_list_member_directory(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    viewer = _client(monkeypatch)
    _signup_login(owner, "member-directory-owner@example.com")
    _signup_login(viewer, "member-directory-viewer@example.com")
    group_id = _create_group(owner, name="Private Roster Group")
    invitation = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "member-directory-viewer@example.com", "role": "viewer"},
    )
    assert invitation.status_code == 201
    token = latest_auth_email_token("member-directory-viewer@example.com", "group_invitation")
    assert viewer.post("/group-invitations/accept", json={"token": token}).status_code == 200

    members = viewer.get(f"/groups/{group_id}/members")

    assert members.status_code == 403


def test_owner_can_list_member_basics_for_role_maintenance(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    recipient = _client(monkeypatch)
    owner_id = _signup_login(owner, "member-list-owner@example.com", nickname="Roster Owner")
    recipient_id = _signup_login(
        recipient,
        "member-list-recipient@example.com",
        nickname="Roster Recipient",
    )
    group_id = _create_group(owner, name="Managed Roster Group")
    invitation = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "member-list-recipient@example.com", "role": "viewer"},
    )
    assert invitation.status_code == 201
    token = latest_auth_email_token("member-list-recipient@example.com", "group_invitation")
    assert recipient.post("/group-invitations/accept", json={"token": token}).status_code == 200

    members = owner.get(f"/groups/{group_id}/members")

    assert members.status_code == 200
    assert {member["user_id"] for member in members.json()} >= {owner_id, recipient_id}
    assert {member["nickname"] for member in members.json()} >= {
        "Roster Owner",
        "Roster Recipient",
    }
    assert all("email" not in member and "profile" not in member for member in members.json())


def test_registered_and_unregistered_invitation_create_responses_match(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    registered = _client(monkeypatch)
    _signup_login(owner, "shape-owner@example.com")
    _signup_login(registered, "registered-target@example.com")
    group_id = _create_group(owner, name="Shape Group")

    registered_response = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "registered-target@example.com", "role": "viewer"},
    )
    unregistered_response = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "unregistered-target@example.com", "role": "viewer"},
    )

    assert registered_response.status_code == 201
    assert unregistered_response.status_code == 201
    registered_payload = registered_response.json()
    unregistered_payload = unregistered_response.json()
    assert registered_payload.keys() == unregistered_payload.keys()
    for payload in (registered_payload, unregistered_payload):
        assert "user_id" not in payload
        assert "account_exists" not in payload
        assert "profile" not in payload
        assert payload["status"] == "pending"


def test_duplicate_cancel_and_reinvite_boundaries(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    recipient = _client(monkeypatch)
    _signup_login(owner, "duplicate-owner@example.com")
    _signup_login(recipient, "duplicate-recipient@example.com")
    group_id = _create_group(owner, name="Duplicate Group")

    created = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "duplicate-recipient@example.com", "role": "viewer"},
    )
    assert created.status_code == 201
    token = latest_auth_email_token("duplicate-recipient@example.com", "group_invitation")
    duplicate = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "DUPLICATE-recipient@example.com", "role": "editor"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "pending_invitation_exists"

    cancelled = owner.delete(f"/groups/{group_id}/invitations/{created.json()['id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert recipient.post("/group-invitations/accept", json={"token": token}).status_code == 400

    reinvited = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "duplicate-recipient@example.com", "role": "editor"},
    )
    assert reinvited.status_code == 201
    assert reinvited.json()["role"] == "editor"


def test_non_manager_cannot_manage_invitations(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    viewer = _client(monkeypatch)
    _signup_login(owner, "non-manager-owner@example.com")
    _signup_login(viewer, "non-manager-viewer@example.com")
    group_id = _create_group(owner, name="Managed Invite Group")
    invitation = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": "non-manager-viewer@example.com", "role": "viewer"},
    )
    assert invitation.status_code == 201
    token = latest_auth_email_token("non-manager-viewer@example.com", "group_invitation")
    assert viewer.post("/group-invitations/accept", json={"token": token}).status_code == 200

    response = viewer.post(
        f"/groups/{group_id}/invitations",
        json={"email": "someone@example.com", "role": "viewer"},
    )

    assert response.status_code == 403


def test_direct_member_creation_route_is_absent_from_product_api(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    recipient = _client(monkeypatch)
    _signup_login(owner, "direct-route-owner@example.com")
    recipient_id = _signup_login(recipient, "direct-route-recipient@example.com")
    group_id = _create_group(owner, name="No Direct Adds")

    response = owner.post(
        f"/groups/{group_id}/members",
        json={"user_id": recipient_id, "role": "viewer"},
    )

    assert response.status_code == 405
    assert _membership_row(group_id, recipient_id) is None
    openapi = owner.get("/openapi.json").json()
    assert "post" not in openapi["paths"]["/groups/{group_id}/members"]
