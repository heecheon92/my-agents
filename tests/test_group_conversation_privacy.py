"""Group-context conversations keep transcripts owner-only."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.conversations.models import AgentRunModel
from my_agents.groups.models import MembershipModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class SpyGraph:
    def invoke(self, input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        return {
            "reply": "private group-context answer",
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
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _assistant_message_id(run_id: str) -> str:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = db.get(AgentRunModel, run_id)
        assert run is not None
        assert run.assistant_message_id is not None
        return run.assistant_message_id
    finally:
        session_generator.close()


def _remove_membership(group_id: str, user_id: str) -> None:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        db.execute(
            delete(MembershipModel).where(
                MembershipModel.group_id == group_id,
                MembershipModel.user_id == user_id,
            )
        )
        db.commit()
    finally:
        session_generator.close()


def test_group_member_cannot_access_another_members_group_context_transcript(
    monkeypatch,
) -> None:  # noqa: ANN001
    owner = _client(monkeypatch, SpyGraph())
    member = _client(monkeypatch, SpyGraph())
    outsider = _client(monkeypatch, SpyGraph())
    owner_id = _signup_login(owner, "group-chat-owner@example.com")
    member_id = _signup_login(member, "group-chat-member@example.com")
    _signup_login(outsider, "group-chat-outsider@example.com")

    group_id = owner.post("/groups", json={"name": "Private Source Group"}).json()["id"]
    assert owner_id
    assert (
        owner.post(
            f"/groups/{group_id}/members", json={"user_id": member_id, "role": "viewer"}
        ).status_code
        == 204
    )
    conversation_id = owner.post(
        "/conversations",
        json={"title": "Owner private group chat", "group_id": group_id},
    ).json()["id"]
    message = owner.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "owner-only group transcript"},
    )
    run = owner.post(f"/conversations/{conversation_id}/runs", json={"message": "Hello"})
    run_id = run.json()["run_id"]
    assistant_message_id = _assistant_message_id(run_id)

    listed_ids = {item["id"] for item in member.get("/conversations").json()}
    assert conversation_id not in listed_ids
    assert message.status_code == 201
    assert run.status_code == 200

    blocked_requests = [
        member.get(f"/conversations/{conversation_id}"),
        member.get(f"/conversations/{conversation_id}/messages"),
        member.get(f"/conversations/{conversation_id}/runs"),
        member.get(f"/conversations/{conversation_id}/runs/{run_id}"),
        member.get(f"/conversations/{conversation_id}/runs/{run_id}/events"),
        member.post(f"/conversations/{conversation_id}/runs", json={"message": "leak?"}),
        member.post(f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay"),
        outsider.get(f"/conversations/{conversation_id}"),
    ]

    assert [response.status_code for response in blocked_requests] == [404] * len(blocked_requests)
    assert all("owner-only group transcript" not in response.text for response in blocked_requests)


def test_removed_group_member_cannot_run_existing_group_context_conversation(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch, SpyGraph())
    user_id = _signup_login(client, "former-member@example.com")
    group_id = client.post("/groups", json={"name": "Former Member Group"}).json()["id"]
    conversation_id = client.post(
        "/conversations",
        json={"title": "Former member group chat", "group_id": group_id},
    ).json()["id"]
    _remove_membership(group_id, user_id)

    response = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Hello"})

    assert response.status_code == 403
    assert response.json() == {"detail": "not allowed"}
