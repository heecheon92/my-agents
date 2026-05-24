"""Group knowledge publish request API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.knowledge.models import DocumentModel, KnowledgePublishRequestModel
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.persistence.database import get_database_session

from .conftest import verify_latest_auth_email


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(create_app())


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _create_group(owner: TestClient, *, name: str = "Publish Group") -> str:
    response = owner.post("/groups", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _add_member(owner: TestClient, group_id: str, user_id: str, role: str = "viewer") -> None:
    response = owner.post(f"/groups/{group_id}/members", json={"user_id": user_id, "role": role})
    assert response.status_code == 204


def _create_personal_kb(client: TestClient, name: str = "Personal KB") -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_group_kb(client: TestClient, group_id: str, name: str = "Group KB") -> str:
    response = client.post(
        "/knowledge-bases",
        json={"name": name, "scope": "group", "group_id": group_id},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_document(client: TestClient, *, kb_id: str, title: str, content: str) -> str:
    response = client.post(
        f"/knowledge-bases/{kb_id}/documents",
        json={"title": title, "content": content},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _ingest(client: TestClient, *, kb_id: str, document_id: str) -> None:
    response = client.post(f"/knowledge-bases/{kb_id}/documents/{document_id}/ingest")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def _group_retrieval_hits(user_id: str, *, kb_id: str, query: str) -> list[str]:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return [
            item.document.id
            for item in RetrievalService(db).retrieve_scoped(
                user_id=user_id,
                query=query,
                knowledge_base_ids=[kb_id],
            )
        ]
    finally:
        session_generator.close()


def test_member_can_create_pending_publish_request_for_own_personal_document(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    requester = _client(monkeypatch)
    _owner_id = _signup_login(owner, "publish-owner@example.com")
    requester_id = _signup_login(requester, "publish-requester@example.com")
    group_id = _create_group(owner)
    _add_member(owner, group_id, requester_id, "viewer")
    target_kb_id = _create_group_kb(owner, group_id)
    personal_kb_id = _create_personal_kb(requester)
    source_document_id = _create_document(
        requester,
        kb_id=personal_kb_id,
        title="Private draft",
        content="Privately held publication candidate.",
    )

    response = requester.post(
        f"/groups/{group_id}/publish-requests",
        json={"source_document_id": source_document_id, "target_knowledge_base_id": target_kb_id},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["requester_user_id"] == requester_id
    assert payload["target_group_id"] == group_id
    assert payload["target_knowledge_base_id"] == target_kb_id
    assert payload["source_document_id"] == source_document_id
    assert payload["status"] == "pending"
    assert payload["reviewer_user_id"] is None
    assert payload["published_document_id"] is None

    requester_list = requester.get(f"/groups/{group_id}/publish-requests")
    assert requester_list.status_code == 200
    assert [item["id"] for item in requester_list.json()] == [payload["id"]]

    owner_list = owner.get(f"/groups/{group_id}/publish-requests")
    assert owner_list.status_code == 200
    assert [item["id"] for item in owner_list.json()] == [payload["id"]]


def test_publish_request_rejects_invalid_source_or_target_boundaries(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    requester = _client(monkeypatch)
    other = _client(monkeypatch)
    _owner_id = _signup_login(owner, "publish-boundary-owner@example.com")
    requester_id = _signup_login(requester, "publish-boundary-requester@example.com")
    _other_id = _signup_login(other, "publish-boundary-other@example.com")
    group_id = _create_group(owner, name="Boundary Group")
    _add_member(owner, group_id, requester_id, "viewer")
    target_group_kb_id = _create_group_kb(owner, group_id, "Boundary Group KB")
    requester_personal_kb_id = _create_personal_kb(requester, "Requester KB")
    other_personal_kb_id = _create_personal_kb(other, "Other KB")
    requester_personal_doc_id = _create_document(
        requester,
        kb_id=requester_personal_kb_id,
        title="Requester personal",
        content="requester owned",
    )
    other_personal_doc_id = _create_document(
        other,
        kb_id=other_personal_kb_id,
        title="Other personal",
        content="other owned",
    )
    group_doc_id = _create_document(
        owner,
        kb_id=target_group_kb_id,
        title="Group source",
        content="already group scoped",
    )
    requester_non_group_target_kb_id = _create_personal_kb(requester, "Not group target")

    not_owned = requester.post(
        f"/groups/{group_id}/publish-requests",
        json={
            "source_document_id": other_personal_doc_id,
            "target_knowledge_base_id": target_group_kb_id,
        },
    )
    assert not_owned.status_code == 404

    non_personal_source = owner.post(
        f"/groups/{group_id}/publish-requests",
        json={"source_document_id": group_doc_id, "target_knowledge_base_id": target_group_kb_id},
    )
    assert non_personal_source.status_code == 422
    assert non_personal_source.json()["detail"] == "source document must be personal"

    non_group_target = requester.post(
        f"/groups/{group_id}/publish-requests",
        json={
            "source_document_id": requester_personal_doc_id,
            "target_knowledge_base_id": requester_non_group_target_kb_id,
        },
    )
    assert non_group_target.status_code == 422
    assert non_group_target.json()["detail"] == "target knowledge base must belong to group"


def test_owner_admin_approval_copies_and_ingests_group_document_without_exposing_original(
    monkeypatch,
) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    requester = _client(monkeypatch)
    viewer = _client(monkeypatch)
    owner_id = _signup_login(owner, "publish-approve-owner@example.com")
    requester_id = _signup_login(requester, "publish-approve-requester@example.com")
    viewer_id = _signup_login(viewer, "publish-approve-viewer@example.com")
    group_id = _create_group(owner, name="Approval Group")
    _add_member(owner, group_id, requester_id, "viewer")
    _add_member(owner, group_id, viewer_id, "viewer")
    target_kb_id = _create_group_kb(owner, group_id, "Approval KB")
    personal_kb_id = _create_personal_kb(requester, "Approval Personal KB")
    source_document_id = _create_document(
        requester,
        kb_id=personal_kb_id,
        title="Personal Alpha",
        content="GKPublishUniqueAlpha is requester-only until approval.",
    )
    _ingest(requester, kb_id=personal_kb_id, document_id=source_document_id)

    request = requester.post(
        f"/groups/{group_id}/publish-requests",
        json={"source_document_id": source_document_id, "target_knowledge_base_id": target_kb_id},
    )
    assert request.status_code == 201
    request_id = request.json()["id"]

    assert _group_retrieval_hits(viewer_id, kb_id=target_kb_id, query="GKPublishUniqueAlpha") == []

    viewer_approve = viewer.post(f"/groups/{group_id}/publish-requests/{request_id}/approve")
    assert viewer_approve.status_code == 403
    assert _group_retrieval_hits(viewer_id, kb_id=target_kb_id, query="GKPublishUniqueAlpha") == []

    approved = owner.post(f"/groups/{group_id}/publish-requests/{request_id}/approve")
    assert approved.status_code == 200
    approved_payload = approved.json()
    assert approved_payload["status"] == "approved"
    assert approved_payload["reviewer_user_id"] == owner_id
    published_document_id = approved_payload["published_document_id"]
    assert published_document_id and published_document_id != source_document_id

    hits = _group_retrieval_hits(viewer_id, kb_id=target_kb_id, query="GKPublishUniqueAlpha")
    assert hits == [published_document_id]

    requester_delete_copy = requester.delete(f"/documents/{published_document_id}")
    assert requester_delete_copy.status_code == 404
    requester_manage_copy = requester.patch(
        f"/documents/{published_document_id}/permissions",
        json={
            "user_id": viewer_id,
            "can_read": True,
            "can_write": True,
            "can_manage": True,
            "can_ingest": True,
        },
    )
    assert requester_manage_copy.status_code == 403
    owner_manage_copy = owner.patch(
        f"/documents/{published_document_id}/permissions",
        json={
            "user_id": viewer_id,
            "can_read": True,
            "can_write": False,
            "can_manage": False,
            "can_ingest": False,
        },
    )
    assert owner_manage_copy.status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        original = db.get(DocumentModel, source_document_id)
        copied = db.get(DocumentModel, published_document_id)
        publish_request = db.scalar(
            select(KnowledgePublishRequestModel).where(
                KnowledgePublishRequestModel.id == request_id
            )
        )
        assert original is not None
        assert copied is not None
        assert publish_request is not None
        assert original.group_id is None
        assert original.knowledge_base_id == personal_kb_id
        assert copied.group_id == group_id
        assert copied.knowledge_base_id == target_kb_id
        assert copied.content == original.content
        assert publish_request.published_document_id == published_document_id
    finally:
        session_generator.close()


def test_rejected_publish_request_has_zero_retrieval_effect(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    requester = _client(monkeypatch)
    viewer = _client(monkeypatch)
    owner_id = _signup_login(owner, "publish-reject-owner@example.com")
    requester_id = _signup_login(requester, "publish-reject-requester@example.com")
    viewer_id = _signup_login(viewer, "publish-reject-viewer@example.com")
    group_id = _create_group(owner, name="Reject Group")
    _add_member(owner, group_id, requester_id, "viewer")
    _add_member(owner, group_id, viewer_id, "viewer")
    target_kb_id = _create_group_kb(owner, group_id, "Reject KB")
    personal_kb_id = _create_personal_kb(requester, "Reject Personal KB")
    source_document_id = _create_document(
        requester,
        kb_id=personal_kb_id,
        title="Personal Beta",
        content="GKPublishUniqueBeta should never appear in group retrieval.",
    )
    _ingest(requester, kb_id=personal_kb_id, document_id=source_document_id)

    request = requester.post(
        f"/groups/{group_id}/publish-requests",
        json={"source_document_id": source_document_id, "target_knowledge_base_id": target_kb_id},
    )
    assert request.status_code == 201
    request_id = request.json()["id"]

    rejected = owner.post(f"/groups/{group_id}/publish-requests/{request_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reviewer_user_id"] == owner_id
    assert rejected.json()["published_document_id"] is None

    assert _group_retrieval_hits(viewer_id, kb_id=target_kb_id, query="GKPublishUniqueBeta") == []
