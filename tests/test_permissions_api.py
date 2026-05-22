"""Group, document, and authorization API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import load_app, verify_latest_auth_email


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(load_app())


def _signup_login(client: TestClient, email: str) -> tuple[str, str]:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
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


def test_group_owner_can_add_member_and_group_viewer_can_read_document(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    viewer = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _owner_id, _ = _signup_login(owner, "owner@example.com")
    viewer_id, _ = _signup_login(viewer, "viewer@example.com")
    _outsider_id, _ = _signup_login(outsider, "outsider@example.com")

    group = owner.post("/groups", json={"name": "Portfolio Team"})
    assert group.status_code == 201
    group_id = group.json()["id"]
    assert group.json()["role"] == "owner"

    add_member = owner.post(
        f"/groups/{group_id}/members",
        json={"user_id": viewer_id, "role": "viewer"},
    )
    assert add_member.status_code == 204
    assert viewer.get(f"/groups/{group_id}").status_code == 200
    kb = owner.post(
        "/knowledge-bases",
        json={"name": "Group KB", "scope": "group", "group_id": group_id},
    )
    assert kb.status_code == 201
    kb_id = kb.json()["id"]

    document = owner.post(
        "/documents",
        json={
            "title": "Group Plan",
            "content": "shared",
            "group_id": group_id,
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    document_id = document.json()["id"]

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
    assert viewer_create.status_code == 403


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


def test_non_manager_cannot_change_group_membership(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    viewer = _client(monkeypatch)
    other = _client(monkeypatch)
    _owner_id, _ = _signup_login(owner, "manager-owner@example.com")
    viewer_id, _ = _signup_login(viewer, "manager-viewer@example.com")
    other_id, _ = _signup_login(other, "manager-other@example.com")
    group_id = owner.post("/groups", json={"name": "Managed Group"}).json()["id"]
    owner.post(f"/groups/{group_id}/members", json={"user_id": viewer_id, "role": "viewer"})

    response = viewer.post(
        f"/groups/{group_id}/members",
        json={"user_id": other_id, "role": "viewer"},
    )

    assert response.status_code == 403
