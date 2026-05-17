"""Server-owned conversation and product run API tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.schemas import RouteDecision


class SpyGraph:
    """Graph spy that records app-owned message state passed to run endpoint."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict) -> dict:  # noqa: A002 - matches LangGraph API
        self.calls.append(input)
        messages = input["messages"]
        return {
            "reply": f"saw {len(messages)} messages",
            "route": RouteDecision(label="general_assistant", explanation="spy route"),
        }


def _client(monkeypatch, graph: SpyGraph | None = None) -> TestClient:  # noqa: ANN001
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
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["id"]


def test_conversation_run_uses_server_owned_history(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "history@example.com")
    conversation = client.post("/conversations", json={"title": "History"})
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]

    first = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Hello"})
    second = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Continue"})

    assert first.status_code == 200
    assert first.json()["reply"] == "saw 1 messages"
    assert second.status_code == 200
    assert second.json()["reply"] == "saw 3 messages"
    assert graph.calls[0]["conversation_id"] == conversation_id
    assert graph.calls[0]["principal_id"]
    assert [message.content for message in graph.calls[1]["messages"]] == [
        "Hello",
        "saw 1 messages",
        "Continue",
    ]


def test_conversation_access_is_scoped_to_owner_or_group_member(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    member = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "conv-owner@example.com")
    member_id = _signup_login(member, "conv-member@example.com")
    _signup_login(outsider, "conv-outsider@example.com")

    group_id = owner.post("/groups", json={"name": "Conversation Group"}).json()["id"]
    owner.post(f"/groups/{group_id}/members", json={"user_id": member_id, "role": "viewer"})
    conversation_id = owner.post(
        "/conversations",
        json={"title": "Group Conversation", "group_id": group_id},
    ).json()["id"]

    assert member.get(f"/conversations/{conversation_id}").status_code == 200
    assert outsider.get(f"/conversations/{conversation_id}").status_code == 404


def test_legacy_assistant_chat_remains_dev_surface_without_product_run_fields(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    response = client.post("/assistant/chat", json={"message": "Hello", "history": []})

    assert response.status_code == 200
    payload = response.json()
    assert payload["handled_by"] == "personal_assistant_graph"
    assert "run_id" not in payload
    assert "citations" not in payload
