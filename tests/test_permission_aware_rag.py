"""Permission-aware RAG and graph expansion tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
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

    private_phrase = "Phoenix Retrieval Kernel"
    document = owner.post(
        "/documents",
        json={
            "title": "Private RAG Plan",
            "content": f"{private_phrase} uses LangGraph for authorized answers.",
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


def test_graph_expansion_adds_authorized_related_chunks(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "rag-expansion@example.com")

    document = client.post(
        "/documents",
        json={
            "title": "LangGraph Expansion Notes",
            "content": "LangGraph retrieval matches AlphaQuery.\n\n"
            "LangGraph planner memory explains follow-up synthesis.",
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
