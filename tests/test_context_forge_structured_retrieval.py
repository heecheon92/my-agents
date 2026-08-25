"""Structured ContextForge retrieval tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.knowledge.models import StructuredKnowledgeEntityModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email
from .rag_spy_helpers import rag_update_for_spy


class ContextForgeSpyGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict, **kwargs: Any) -> dict:  # noqa: A002
        rag_update = rag_update_for_spy(input, kwargs)
        self.calls.append({**input, **rag_update})
        return {
            **rag_update,
            "reply": "Here are the documented endpoints from the authorized context.",
            "route": RouteDecision(label="general_assistant", explanation="spy route"),
        }


def _client(monkeypatch, graph: ContextForgeSpyGraph | None = None) -> TestClient:  # noqa: ANN001
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


def _create_conversation(client: TestClient) -> str:
    response = client.post("/conversations", json={"title": "ContextForge"})
    assert response.status_code == 201
    return response.json()["id"]


def test_context_forge_retrieves_structured_api_endpoints(monkeypatch) -> None:  # noqa: ANN001
    graph = ContextForgeSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "contextforge-owner@example.com")
    kb = client.post("/knowledge-bases", json={"name": "API Docs", "scope": "personal"})
    assert kb.status_code == 201
    document = client.post(
        "/documents",
        json={
            "title": "API Reference",
            "knowledge_base_id": kb.json()["id"],
            "content": """
Authentication
POST /auth/login

Users
GET /users
PATCH /projects/{id}
""".strip(),
        },
    )
    assert document.status_code == 201
    ingest = client.post(f"/documents/{document.json()['id']}/ingest")
    assert ingest.status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        entities = db.scalars(
            select(StructuredKnowledgeEntityModel).where(
                StructuredKnowledgeEntityModel.document_id == document.json()["id"],
                StructuredKnowledgeEntityModel.entity_type == "api_endpoint",
            )
        ).all()
    finally:
        session_generator.close()
    assert {entity.label for entity in entities} == {
        "POST /auth/login",
        "GET /users",
        "PATCH /projects/{id}",
    }

    conversation_id = _create_conversation(client)
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "List the API endpoints in this document"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval_route"] == "retrieval_required"
    assert payload["answer_mode"] == "document_grounded"
    assert payload["consulted_sources"]
    assert graph.calls[-1]["retrieved_context"]
    assert graph.calls[-1]["retrieved_context"][0]["source"].startswith("structured_entity:")
    snippets = "\n".join(item["snippet"] for item in graph.calls[-1]["retrieved_context"])
    assert "POST /auth/login" in snippets
    assert "GET /users" in snippets

    events = client.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}/events")
    assert events.status_code == 200
    retrieval_event = next(
        event for event in events.json() if event["event_type"] == "retrieval_completed"
    )
    assert retrieval_event["payload"]["contextforge_intent"] == "enumeration"
    assert retrieval_event["payload"]["contextforge_reranker"] == "deterministic"
    assert retrieval_event["payload"]["structured_entity_count"] >= 1
    assert retrieval_event["payload"]["structured_entity_types"] == ["api_endpoint"]


def test_structured_entities_from_unauthorized_documents_are_not_used(monkeypatch) -> None:  # noqa: ANN001
    graph = ContextForgeSpyGraph()
    owner = _client(monkeypatch)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "contextforge-private-owner@example.com")
    _signup_login(outsider, "contextforge-private-outsider@example.com")

    owner_kb = owner.post("/knowledge-bases", json={"name": "Private API", "scope": "personal"})
    assert owner_kb.status_code == 201
    private_doc = owner.post(
        "/documents",
        json={
            "title": "Private API Reference",
            "knowledge_base_id": owner_kb.json()["id"],
            "content": "POST /secret/admin\nGET /private/users",
        },
    )
    assert private_doc.status_code == 201
    private_ingest = owner.post(f"/documents/{private_doc.json()['id']}/ingest")
    assert private_ingest.status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        private_entities = db.scalars(
            select(StructuredKnowledgeEntityModel).where(
                StructuredKnowledgeEntityModel.document_id == private_doc.json()["id"],
                StructuredKnowledgeEntityModel.entity_type == "api_endpoint",
            )
        ).all()
    finally:
        session_generator.close()
    assert {entity.label for entity in private_entities} == {
        "POST /secret/admin",
        "GET /private/users",
    }

    conversation_id = _create_conversation(outsider)
    response = outsider.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "List the API endpoints in this document"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval_route"] == "retrieval_required"
    assert payload["answer_mode"] == "general_knowledge"
    assert payload["citations"] == []
    assert "enough relevant authorized document evidence" in payload["reply"]
    assert graph.calls[-1]["rag_halt_before_response"] is True
    assert graph.calls[-1]["retrieval_route"] == "retrieval_required"
    assert graph.calls[-1]["retrieved_context"] == []

    events = outsider.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}/events")
    assert events.status_code == 200
    retrieval_event = next(
        event for event in events.json() if event["event_type"] == "retrieval_completed"
    )
    assert retrieval_event["payload"]["structured_entity_count"] == 0
    assert retrieval_event["payload"]["candidate_count"] == 0
    assert retrieval_event["payload"]["authorized_context_count"] == 0
    assert retrieval_event["payload"]["retrieval_attempt_count"] == 2
    assert retrieval_event["payload"]["retrieval_retry_count"] == 1
    assert retrieval_event["payload"]["insufficient_evidence"] is True
    assert "secret" not in str(retrieval_event["payload"]).casefold()
    event_types = [event["event_type"] for event in events.json()]
    assert event_types == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "answer_composed",
    ]
    answer_event = next(
        event for event in events.json() if event["event_type"] == "answer_composed"
    )
    assert answer_event["payload"]["insufficient_evidence"] is True
