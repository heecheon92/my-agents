"""System knowledge-base management and ambient retrieval contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.auth.models import UserModel
from my_agents.knowledge.models import KnowledgeBaseModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email
from .rag_spy_helpers import rag_update_for_spy


class SystemKnowledgeSpyGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict, **kwargs: Any) -> dict:  # noqa: A002
        rag_update = rag_update_for_spy(input, kwargs)
        self.calls.append({**input, **rag_update})
        return {
            **rag_update,
            "reply": "system knowledge graph response",
            "route": RouteDecision(label="general_assistant", explanation="system kb spy"),
        }


def _client(
    monkeypatch: pytest.MonkeyPatch,
    graph: SystemKnowledgeSpyGraph | None = None,
) -> TestClient:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    app = create_app()
    if graph is not None:
        app.dependency_overrides[get_graph_runner] = lambda: graph
    return TestClient(app)


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post(
        "/auth/signup",
        json={"email": email, "nickname": "Test User", "password": password},
    )
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _set_user_type(user_id: str, user_type: str) -> None:
    assert hasattr(UserModel, "user_type"), "users.user_type column must exist"
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        user = db.scalar(select(UserModel).where(UserModel.id == user_id))
        assert user is not None
        user.user_type = user_type
        db.commit()
    finally:
        session_generator.close()


def _system_kb_count() -> int:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return len(
            db.scalars(select(KnowledgeBaseModel).where(KnowledgeBaseModel.scope == "system")).all()
        )
    finally:
        session_generator.close()


def test_system_scope_schema_and_api_are_privilege_gated(monkeypatch) -> None:  # noqa: ANN001
    root_client = _client(monkeypatch)
    normal_client = _client(monkeypatch)
    root_user_id = _signup_login(root_client, "root-system-kb@example.com")
    _signup_login(normal_client, "normal-system-kb@example.com")
    _set_user_type(root_user_id, "root")

    normal_create = normal_client.post(
        "/knowledge-bases",
        json={"name": "Project Facts", "scope": "system"},
    )
    assert normal_create.status_code == 403
    assert _system_kb_count() == 0

    root_create = root_client.post(
        "/knowledge-bases",
        json={"name": "Project Facts", "scope": "system"},
    )
    assert root_create.status_code == 201
    system_kb = root_create.json()
    assert system_kb["scope"] == "system"
    assert system_kb["owner_user_id"] == root_user_id
    assert system_kb["group_id"] is None
    assert system_kb["purpose"] == "standard"

    root_list = root_client.get("/knowledge-bases")
    normal_list = normal_client.get("/knowledge-bases")

    assert root_list.status_code == 200
    assert any(kb["id"] == system_kb["id"] for kb in root_list.json())
    assert normal_list.status_code == 200
    assert all(kb["id"] != system_kb["id"] for kb in normal_list.json())

    normal_get = normal_client.get(f"/knowledge-bases/{system_kb['id']}")
    root_get = root_client.get(f"/knowledge-bases/{system_kb['id']}")

    assert normal_get.status_code == 404
    assert root_get.status_code == 200
    assert root_get.json()["id"] == system_kb["id"]


def test_system_kb_document_writes_are_manager_only(monkeypatch) -> None:  # noqa: ANN001
    root_client = _client(monkeypatch)
    normal_client = _client(monkeypatch)
    root_user_id = _signup_login(root_client, "root-doc-system-kb@example.com")
    _signup_login(normal_client, "normal-doc-system-kb@example.com")
    _set_user_type(root_user_id, "system")

    system_kb = root_client.post(
        "/knowledge-bases",
        json={"name": "Public Project Docs", "scope": "system"},
    )
    assert system_kb.status_code == 201
    system_kb_id = system_kb.json()["id"]

    normal_nested_create = normal_client.post(
        f"/knowledge-bases/{system_kb_id}/documents",
        json={"title": "Attempted Doc", "content": "normal user should not write"},
    )
    assert normal_nested_create.status_code == 404

    root_nested_create = root_client.post(
        f"/knowledge-bases/{system_kb_id}/documents",
        json={"title": "Project Stack", "content": "my-agents uses FastAPI."},
    )
    assert root_nested_create.status_code == 201
    system_doc = root_nested_create.json()
    assert system_doc["knowledge_base_id"] == system_kb_id
    assert system_doc["owner_user_id"] == root_user_id
    assert system_doc["group_id"] is None

    normal_direct_delete = normal_client.delete(f"/documents/{system_doc['id']}")
    assert normal_direct_delete.status_code == 404


def test_selected_personal_chat_keeps_ambient_system_knowledge(monkeypatch) -> None:  # noqa: ANN001
    graph = SystemKnowledgeSpyGraph()
    root_client = _client(monkeypatch, graph)
    normal_client = _client(monkeypatch, graph)
    root_user_id = _signup_login(root_client, "root-ambient-system-kb@example.com")
    _signup_login(normal_client, "normal-ambient-system-kb@example.com")
    _set_user_type(root_user_id, "root")

    system_kb = root_client.post(
        "/knowledge-bases",
        json={"name": "Ambient Project Facts", "scope": "system"},
    )
    assert system_kb.status_code == 201
    system_doc = root_client.post(
        f"/knowledge-bases/{system_kb.json()['id']}/documents",
        json={"title": "Creator", "content": "The my-agents project was created by Heecheon."},
    )
    assert system_doc.status_code == 201
    assert root_client.post(f"/documents/{system_doc.json()['id']}/ingest").status_code == 200

    personal_kb = normal_client.post(
        "/knowledge-bases",
        json={"name": "Personal Notes", "scope": "personal"},
    )
    assert personal_kb.status_code == 201
    conversation = normal_client.post("/conversations", json={"title": "Ambient system"})
    assert conversation.status_code == 201

    run = normal_client.post(
        f"/conversations/{conversation.json()['id']}/runs",
        json={
            "message": "Who created this project?",
            "knowledge_base_selection": {
                "mode": "selected",
                "knowledge_base_ids": [personal_kb.json()["id"]],
            },
        },
    )

    assert run.status_code == 200
    payload = run.json()
    assert payload["retrieval_route"] in {"retrieval_optional", "retrieval_required"}
    assert payload["resolved_knowledge_base_count"] == 1
    assert "ambient_system_knowledge_base_count" not in payload
    assert payload["citations"] == []
    assert system_kb.json()["id"] not in payload["resolved_knowledge_base_ids"]
    assert "memory" not in str(payload.get("source_snapshot", {})).lower()
