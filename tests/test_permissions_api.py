"""Group, document, and authorization API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import latest_auth_email_token, load_app, verify_latest_auth_email


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(load_app())


def _signup_login(client: TestClient, email: str) -> tuple[str, str]:
    password = "correct horse battery staple"
    signup = client.post(
        "/auth/signup", json={"email": email, "nickname": "Test User", "password": password}
    )
    assert signup.status_code == 201
    user_id = signup.json()["user"]["id"]
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return user_id, login.json()["csrf_token"]


def _create_personal_kb(client: TestClient, name: str = "Test KB") -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _invite_and_accept(
    *,
    owner: TestClient,
    recipient: TestClient,
    group_id: str,
    recipient_email: str,
    role: str = "viewer",
) -> None:
    invitation = owner.post(
        f"/groups/{group_id}/invitations",
        json={"email": recipient_email, "role": role},
    )
    assert invitation.status_code == 201
    token = latest_auth_email_token(recipient_email, "group_invitation")
    accepted = recipient.post("/group-invitations/accept", json={"token": token})
    assert accepted.status_code == 200


def _publish_personal_document(
    *,
    owner: TestClient,
    requester: TestClient,
    group_id: str,
    target_kb_id: str,
    personal_kb_id: str,
    title: str,
    content: str,
) -> str:
    source_document = requester.post(
        f"/knowledge-bases/{personal_kb_id}/documents",
        json={"title": title, "content": content},
    )
    assert source_document.status_code == 201
    publish_request = requester.post(
        f"/groups/{group_id}/publish-requests",
        json={
            "source_document_id": source_document.json()["id"],
            "target_knowledge_base_id": target_kb_id,
        },
    )
    assert publish_request.status_code == 201
    approved = owner.post(
        f"/groups/{group_id}/publish-requests/{publish_request.json()['id']}/approve"
    )
    assert approved.status_code == 200
    published_document_id = approved.json()["published_document_id"]
    assert published_document_id
    return published_document_id


def test_group_owner_can_invite_member_and_group_viewer_can_read_document(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    viewer = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _owner_id, _ = _signup_login(owner, "owner@example.com")
    _viewer_id, _ = _signup_login(viewer, "viewer@example.com")
    _outsider_id, _ = _signup_login(outsider, "outsider@example.com")

    group = owner.post("/groups", json={"name": "Demo Team"})
    assert group.status_code == 201
    group_id = group.json()["id"]
    assert group.json()["role"] == "owner"

    _invite_and_accept(
        owner=owner,
        recipient=viewer,
        group_id=group_id,
        recipient_email="viewer@example.com",
        role="viewer",
    )
    assert viewer.get(f"/groups/{group_id}").status_code == 200
    kb = owner.post(
        "/knowledge-bases",
        json={"name": "Group KB", "scope": "group", "group_id": group_id},
    )
    assert kb.status_code == 201
    kb_id = kb.json()["id"]
    owner_personal_kb_id = _create_personal_kb(owner, "Owner Source KB")
    document_id = _publish_personal_document(
        owner=owner,
        requester=owner,
        group_id=group_id,
        target_kb_id=kb_id,
        personal_kb_id=owner_personal_kb_id,
        title="Group Plan",
        content="shared",
    )

    assert viewer.get(f"/documents/{document_id}").status_code == 200
    assert outsider.get(f"/documents/{document_id}").status_code == 404

    viewer_create = viewer.post(
        "/documents",
        json={
            "title": "Viewer Write",
            "content": "no",
            "group_id": group_id,
            "knowledge_base_id": kb_id,
        },
    )
    assert viewer_create.status_code == 422
    assert (
        viewer_create.json()["detail"]
        == "group knowledge bases accept documents through publish approval"
    )


def test_document_owner_can_grant_explicit_user_read_permission(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    reader = _client(monkeypatch)
    _owner_id, _ = _signup_login(owner, "doc-owner@example.com")
    reader_id, _ = _signup_login(reader, "reader@example.com")
    kb_id = _create_personal_kb(owner, "Permission KB")

    document = owner.post(
        "/documents",
        json={
            "title": "Personal Note",
            "content": "private",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    document_id = document.json()["id"]

    assert reader.get(f"/documents/{document_id}").status_code == 404

    grant = owner.patch(
        f"/documents/{document_id}/permissions",
        json={"user_id": reader_id, "can_read": True},
    )
    assert grant.status_code == 200
    assert grant.json()["can_read"] is True

    readable = reader.get(f"/documents/{document_id}")
    assert readable.status_code == 200
    assert readable.json()["title"] == "Personal Note"


def test_non_manager_cannot_create_group_invitations(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    viewer = _client(monkeypatch)
    other = _client(monkeypatch)
    _owner_id, _ = _signup_login(owner, "manager-owner@example.com")
    _viewer_id, _ = _signup_login(viewer, "manager-viewer@example.com")
    _other_id, _ = _signup_login(other, "manager-other@example.com")
    group_id = owner.post("/groups", json={"name": "Managed Group"}).json()["id"]
    _invite_and_accept(
        owner=owner,
        recipient=viewer,
        group_id=group_id,
        recipient_email="manager-viewer@example.com",
        role="viewer",
    )

    response = viewer.post(
        f"/groups/{group_id}/invitations",
        json={"email": "manager-other@example.com", "role": "viewer"},
    )

    assert response.status_code == 403
