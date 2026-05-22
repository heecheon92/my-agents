"""Permission-aware RAG and graph expansion tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

import my_agents.knowledge.extraction as extraction_module
import my_agents.knowledge.retrieval as retrieval_module
from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.knowledge.retrieval import _postgres_vector_authorized_statement
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class RagSpyGraph:
    """Graph spy that keeps product RAG composition deterministic in tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict) -> dict:  # noqa: A002 - matches LangGraph API
        self.calls.append(input)
        return {
            "reply": "graph reply without hidden document text",
            "route": RouteDecision(label="general_assistant", explanation="spy route"),
        }


class SemanticFakeEmbeddingProvider:
    provider = "fake"
    model = "semantic-fake-v1"
    dimensions = 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        normalized = text.casefold()
        if any(term in normalized for term in ("automobile", "vehicle", "car", "cars")):
            return [1.0, 0.0]
        if any(term in normalized for term in ("pastry", "baking", "bread")):
            return [0.0, 1.0]
        return [0.5, 0.5]


def _client(monkeypatch, graph: RagSpyGraph | None = None) -> TestClient:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    app = create_app()
    if graph is not None:
        app.dependency_overrides[get_graph_runner] = lambda: graph
    return TestClient(app)


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _create_personal_knowledge_base(client: TestClient, name: str = "Test KB") -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_conversation(client: TestClient, title: str = "RAG") -> str:
    response = client.post("/conversations", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def test_chat_run_cites_only_authorized_personal_knowledge(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "rag-owner@example.com")
    _signup_login(outsider, "rag-outsider@example.com")
    kb_id = _create_personal_knowledge_base(owner, "RAG Owner KB")

    private_phrase = "Phoenix Retrieval Kernel"
    document = owner.post(
        "/documents",
        json={
            "title": "Private RAG Plan",
            "content": f"{private_phrase} uses LangGraph for authorized answers.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    ingest = owner.post(f"/documents/{document.json()['id']}/ingest")
    assert ingest.status_code == 200

    owner_conversation_id = _create_conversation(owner, "Owner RAG")
    owner_run = owner.post(
        f"/conversations/{owner_conversation_id}/runs",
        json={"message": "How does the Phoenix retrieval work?"},
    )
    assert owner_run.status_code == 200
    owner_payload = owner_run.json()
    assert owner_payload["citations"]
    assert owner_payload["citations"][0]["document_id"] == document.json()["id"]
    assert private_phrase in owner_payload["reply"]
    assert private_phrase in owner_payload["citations"][0]["snippet"]
    assert graph.calls[-1]["retrieved_chunk_ids"]
    owner_detail = owner.get(
        f"/conversations/{owner_conversation_id}/runs/{owner_payload['run_id']}"
    )
    assert owner_detail.status_code == 200
    assert owner_detail.json() == owner_payload

    outsider_conversation_id = _create_conversation(outsider, "Outsider RAG")
    outsider_run = outsider.post(
        f"/conversations/{outsider_conversation_id}/runs",
        json={"message": "How does the Phoenix retrieval work?"},
    )
    assert outsider_run.status_code == 200
    outsider_payload = outsider_run.json()
    assert outsider_payload["citations"] == []
    assert private_phrase not in outsider_payload["reply"]
    assert graph.calls[-1]["retrieved_chunk_ids"] == []


def test_broad_resume_question_uses_recent_authorized_document(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "resume-owner@example.com")
    _signup_login(outsider, "resume-outsider@example.com")
    kb_id = _create_personal_knowledge_base(owner, "Resume KB")

    resume_phrase = "Heecheon Park builds FastAPI LangGraph portfolio systems"
    document = owner.post(
        "/documents",
        json={
            "title": "Resume 2026",
            "content": f"{resume_phrase} with permission-aware document retrieval.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert owner.post(f"/documents/{document.json()['id']}/ingest").status_code == 200

    owner_conversation_id = _create_conversation(owner, "Resume chat")
    owner_run = owner.post(
        f"/conversations/{owner_conversation_id}/runs",
        json={"message": "Tell me about me from my uploaded resume."},
    )

    assert owner_run.status_code == 200
    owner_payload = owner_run.json()
    assert owner_payload["citations"]
    assert owner_payload["citations"][0]["document_id"] == document.json()["id"]
    assert resume_phrase in owner_payload["reply"]
    assert graph.calls[-1]["retrieved_chunk_ids"]
    assert graph.calls[-1]["retrieved_context"][0]["title"] == "Resume 2026"
    assert resume_phrase in graph.calls[-1]["retrieved_context"][0]["snippet"]

    outsider_conversation_id = _create_conversation(outsider, "No resume leak")
    outsider_run = outsider.post(
        f"/conversations/{outsider_conversation_id}/runs",
        json={"message": "Tell me about me from my uploaded resume."},
    )

    assert outsider_run.status_code == 200
    outsider_payload = outsider_run.json()
    assert outsider_payload["citations"] == []
    assert resume_phrase not in outsider_payload["reply"]
    assert graph.calls[-1]["retrieved_chunk_ids"] == []
    assert graph.calls[-1]["retrieved_context"] == []


def test_semantic_vector_retrieval_after_permission_filtering(monkeypatch) -> None:  # noqa: ANN001
    fake_embeddings = SemanticFakeEmbeddingProvider()
    monkeypatch.setattr(extraction_module, "get_embedding_provider", lambda: fake_embeddings)
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: fake_embeddings)
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "semantic-owner@example.com")
    _signup_login(outsider, "semantic-outsider@example.com")
    kb_id = _create_personal_knowledge_base(owner, "Semantic KB")

    vehicle_doc = owner.post(
        "/documents",
        json={
            "title": "Vehicle Notes",
            "content": "Automobile maintenance schedule uses quarterly inspections.",
            "knowledge_base_id": kb_id,
        },
    )
    pastry_doc = owner.post(
        "/documents",
        json={
            "title": "Pastry Notes",
            "content": "Pastry dough proofing depends on warm kitchen timing.",
            "knowledge_base_id": kb_id,
        },
    )
    assert vehicle_doc.status_code == 201
    assert pastry_doc.status_code == 201
    assert owner.post(f"/documents/{vehicle_doc.json()['id']}/ingest").status_code == 200
    assert owner.post(f"/documents/{pastry_doc.json()['id']}/ingest").status_code == 200

    owner_conversation_id = _create_conversation(owner, "Semantic owner")
    owner_run = owner.post(
        f"/conversations/{owner_conversation_id}/runs",
        json={"message": "What does my uploaded document say about cars?"},
    )

    assert owner_run.status_code == 200
    owner_payload = owner_run.json()
    assert owner_payload["citations"]
    assert owner_payload["citations"][0]["document_id"] == vehicle_doc.json()["id"]
    assert "Automobile maintenance" in owner_payload["reply"]
    assert graph.calls[-1]["retrieved_context"][0]["title"] == "Vehicle Notes"

    outsider_conversation_id = _create_conversation(outsider, "Semantic outsider")
    outsider_run = outsider.post(
        f"/conversations/{outsider_conversation_id}/runs",
        json={"message": "What does my uploaded document say about cars?"},
    )

    assert outsider_run.status_code == 200
    assert outsider_run.json()["citations"] == []
    assert graph.calls[-1]["retrieved_context"] == []


def test_postgres_vector_statement_filters_permissions_before_vector_ordering() -> None:
    statement = _postgres_vector_authorized_statement(
        user_id="user-1",
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "document_chunks.embedding_vector <=>" in sql
    assert "document_chunks.embedding_vector IS NOT NULL" in sql
    assert "documents.owner_user_id" in sql
    assert "memberships.user_id" in sql
    assert "document_permissions.user_id" in sql
    assert "ORDER BY vector_distance" in sql


def test_graph_expansion_adds_authorized_related_chunks(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "rag-expansion@example.com")
    kb_id = _create_personal_knowledge_base(client, "Expansion KB")

    document = client.post(
        "/documents",
        json={
            "title": "LangGraph Expansion Notes",
            "content": "LangGraph retrieval matches AlphaQuery.\n\n"
            "LangGraph planner memory explains follow-up synthesis.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    ingest = client.post(f"/documents/{document.json()['id']}/ingest")
    assert ingest.status_code == 200
    assert ingest.json()["chunk_count"] == 2

    conversation_id = _create_conversation(client, "Expansion")
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "AlphaQuery"},
    )

    assert response.status_code == 200
    payload = response.json()
    snippets = [citation["snippet"] for citation in payload["citations"]]
    assert len(snippets) == 2
    assert any("AlphaQuery" in snippet for snippet in snippets)
    assert any("planner memory" in snippet for snippet in snippets)
    assert "planner memory" in payload["reply"]
    assert len(graph.calls[-1]["retrieved_chunk_ids"]) == 2
