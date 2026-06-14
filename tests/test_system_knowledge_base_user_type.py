"""System knowledge base and platform user-type regressions."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.auth.models import UserModel
from my_agents.knowledge.models import DocumentModel, KnowledgeBaseModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class SystemKbSpyGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict) -> dict:  # noqa: A002
        self.calls.append(input)
        return {
            "reply": "system kb graph response",
            "route": RouteDecision(label="general_assistant", explanation="system kb spy"),
        }


def _client(monkeypatch, graph: SystemKbSpyGraph | None = None) -> TestClient:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    app = create_app()
    if graph is not None:
        app.dependency_overrides[get_graph_runner] = lambda: graph
    return TestClient(app)


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post(
        "/auth/signup", json={"email": email, "nickname": "Test User", "password": password}
    )
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _set_user_type(user_id: str, user_type: str) -> None:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        user = db.get(UserModel, user_id)
        assert user is not None
        user.user_type = user_type
        db.add(user)
        db.commit()
    finally:
        session_generator.close()


def test_auth_me_exposes_read_only_system_knowledge_capability(monkeypatch) -> None:  # noqa: ANN001
    normal = _client(monkeypatch)
    root = _client(monkeypatch)
    normal_id = _signup_login(normal, "system-normal-auth@example.com")
    root_id = _signup_login(root, "system-root-auth@example.com")
    _set_user_type(root_id, "root")

    normal_me = normal.get("/auth/me")
    root_me = root.get("/auth/me")
    forbidden_mutation = root.patch(
        "/auth/me/nickname",
        json={
            "current_password": "correct horse battery staple",
            "nickname": "Root User",
            "user_type": "normal",
        },
    )

    assert normal_id
    assert normal_me.status_code == 200
    assert "user_type" not in normal_me.json()
    assert "can_manage_system_knowledge" not in normal_me.json()
    assert root_me.status_code == 200
    assert root_me.json()["user_type"] == "root"
    assert root_me.json()["can_manage_system_knowledge"] is True
    assert forbidden_mutation.status_code == 422


def test_system_kb_management_is_manager_only_and_hidden_from_normal_lists(monkeypatch) -> None:  # noqa: ANN001
    root = _client(monkeypatch)
    normal = _client(monkeypatch)
    root_id = _signup_login(root, "system-kb-root@example.com")
    _signup_login(normal, "system-kb-normal@example.com")
    _set_user_type(root_id, "system")

    normal_create = normal.post("/knowledge-bases", json={"name": "System KB", "scope": "system"})
    created = root.post("/knowledge-bases", json={"name": "System KB", "scope": "system"})
    system_id = created.json()["id"]
    normal_list = normal.get("/knowledge-bases")
    root_list = root.get("/knowledge-bases")
    normal_get = normal.get(f"/knowledge-bases/{system_id}")
    renamed = root.patch(f"/knowledge-bases/{system_id}", json={"name": "Project Facts"})

    assert normal_create.status_code == 403
    assert created.status_code == 201
    assert created.json()["scope"] == "system"
    assert created.json()["owner_user_id"] == root_id
    assert created.json()["group_id"] is None
    assert system_id not in {item["id"] for item in normal_list.json()}
    assert system_id in {item["id"] for item in root_list.json()}
    assert normal_get.status_code == 404
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Project Facts"


def test_system_documents_are_manager_only_but_ambient_in_chat_retrieval(monkeypatch) -> None:  # noqa: ANN001
    graph = SystemKbSpyGraph()
    root = _client(monkeypatch, graph)
    normal = _client(monkeypatch, graph)
    root_id = _signup_login(root, "system-doc-root@example.com")
    _signup_login(normal, "system-doc-normal@example.com")
    _set_user_type(root_id, "root")

    created = root.post("/knowledge-bases", json={"name": "System KB", "scope": "system"})
    system_id = created.json()["id"]
    document = root.post(
        f"/knowledge-bases/{system_id}/documents",
        json={
            "title": "Project Facts",
            "content": "SystemOnlyFact: my-agents uses FastAPI and LangGraph.",
        },
    )
    document_id = document.json()["id"]
    ingest = root.post(f"/knowledge-bases/{system_id}/documents/{document_id}/ingest")
    normal_doc_list = normal.get(f"/knowledge-bases/{system_id}/documents")
    normal_direct_get = normal.get(f"/documents/{document_id}")
    root_direct_patch = root.patch(
        f"/documents/{document_id}", json={"title": "Updated Project Facts"}
    )
    conversation_id = normal.post("/conversations", json={"title": "System chat"}).json()["id"]
    run = normal.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "What is SystemOnlyFact?"},
    )

    assert document.status_code == 201
    assert ingest.status_code == 200
    assert normal_doc_list.status_code == 404
    assert normal_direct_get.status_code == 404
    assert root_direct_patch.status_code == 200
    assert run.status_code == 200
    payload = run.json()
    assert payload["knowledge_base_selection"] == {"mode": "all", "knowledge_base_ids": []}
    assert payload["resolved_knowledge_base_ids"] == []
    assert payload["resolved_knowledge_base_count"] == 0
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {system_id}
    assert graph.calls[-1]["retrieved_context"][0]["document_id"] == document_id


def test_selected_personal_kb_retrieval_keeps_system_kb_ambient_and_unlisted(monkeypatch) -> None:  # noqa: ANN001
    graph = SystemKbSpyGraph()
    root = _client(monkeypatch, graph)
    normal = _client(monkeypatch, graph)
    root_id = _signup_login(root, "system-selected-root@example.com")
    _signup_login(normal, "system-selected-normal@example.com")
    _set_user_type(root_id, "root")

    system_kb = root.post("/knowledge-bases", json={"name": "System KB", "scope": "system"})
    system_id = system_kb.json()["id"]
    system_doc = root.post(
        f"/knowledge-bases/{system_id}/documents",
        json={"title": "System", "content": "AmbientSystemFact lives in public project facts."},
    )
    assert (
        root.post(
            f"/knowledge-bases/{system_id}/documents/{system_doc.json()['id']}/ingest"
        ).status_code
        == 200
    )
    personal_kb = normal.post("/knowledge-bases", json={"name": "Personal KB", "scope": "personal"})
    personal_id = personal_kb.json()["id"]
    personal_doc = normal.post(
        f"/knowledge-bases/{personal_id}/documents",
        json={"title": "Personal", "content": "PersonalSelectedFact lives in my notes."},
    )
    assert (
        normal.post(
            f"/knowledge-bases/{personal_id}/documents/{personal_doc.json()['id']}/ingest"
        ).status_code
        == 200
    )
    guess_conversation_id = normal.post("/conversations", json={"title": "Guess"}).json()["id"]
    guessed_system = normal.post(
        f"/conversations/{guess_conversation_id}/runs",
        json={
            "message": "Can I select this?",
            "knowledge_base_selection": {
                "mode": "selected",
                "knowledge_base_ids": [system_id],
            },
        },
    )
    conversation_id = normal.post("/conversations", json={"title": "Selected"}).json()["id"]
    run = normal.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Compare AmbientSystemFact and PersonalSelectedFact.",
            "knowledge_base_selection": {
                "mode": "selected",
                "knowledge_base_ids": [personal_id],
            },
        },
    )

    assert guessed_system.status_code == 404
    assert run.status_code == 200
    payload = run.json()
    assert payload["knowledge_base_selection"] == {
        "mode": "selected",
        "knowledge_base_ids": [personal_id],
    }
    assert payload["resolved_knowledge_base_ids"] == [personal_id]
    assert payload["resolved_knowledge_base_count"] == 1
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {
        personal_id,
        system_id,
    }


def test_system_kb_delete_removes_documents_and_dependent_rows(monkeypatch) -> None:  # noqa: ANN001
    root = _client(monkeypatch)
    root_id = _signup_login(root, "system-delete-root@example.com")
    _set_user_type(root_id, "root")
    system_kb = root.post("/knowledge-bases", json={"name": "Delete Me", "scope": "system"})
    system_id = system_kb.json()["id"]
    document = root.post(
        f"/knowledge-bases/{system_id}/documents",
        json={"title": "Delete", "content": "delete me"},
    )

    deleted = root.delete(f"/knowledge-bases/{system_id}")

    assert document.status_code == 201
    assert deleted.status_code == 204
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        assert db.get(KnowledgeBaseModel, system_id) is None
        assert db.get(DocumentModel, document.json()["id"]) is None
    finally:
        session_generator.close()
