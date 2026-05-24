"""Group chat source selection keeps group KB mandatory and personal KBs explicit."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.groups.models import MembershipModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class RagSpyGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
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


def _create_personal_kb(client: TestClient, name: str) -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_group_kb(client: TestClient, group_id: str, name: str) -> str:
    response = client.post(
        "/knowledge-bases",
        json={"name": name, "scope": "group", "group_id": group_id},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_document(client: TestClient, *, title: str, content: str, kb_id: str) -> str:
    response = client.post(
        "/documents",
        json={"title": title, "content": content, "knowledge_base_id": kb_id},
    )
    assert response.status_code == 201
    document_id = response.json()["id"]
    assert client.post(f"/documents/{document_id}/ingest").status_code == 200
    return document_id


def _remove_group_membership(group_id: str, user_id: str) -> None:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        membership = db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == group_id,
                MembershipModel.user_id == user_id,
            )
        )
        assert membership is not None
        db.delete(membership)
        db.commit()
    finally:
        session_generator.close()


def test_group_chat_all_uses_mandatory_group_kb_only(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "group-source-owner@example.com")
    group_id = client.post("/groups", json={"name": "Source Group"}).json()["id"]
    group_kb = _create_group_kb(client, group_id, "Mandatory Group KB")
    personal_kb = _create_personal_kb(client, "Private Personal KB")
    group_document = _create_document(
        client,
        title="Group Source",
        content="GroupAlpha mandatory group source note.",
        kb_id=group_kb,
    )
    personal_document = _create_document(
        client,
        title="Private Source",
        content="PersonalAlpha private personal note.",
        kb_id=personal_kb,
    )
    conversation_id = client.post(
        "/conversations", json={"title": "Group source chat", "group_id": group_id}
    ).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Tell me about my uploaded document."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_context_group_id"] == group_id
    assert payload["mandatory_group_knowledge_base_ids"] == [group_kb]
    assert payload["optional_personal_knowledge_base_ids"] == []
    assert payload["resolved_knowledge_base_ids"] == [group_kb]
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {group_kb}
    assert {context["document_id"] for context in graph.calls[-1]["retrieved_context"]} == {
        group_document
    }
    assert personal_document not in {
        context["document_id"] for context in graph.calls[-1]["retrieved_context"]
    }


def test_group_chat_optional_personal_kb_is_explicit_and_private(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    other = _client(monkeypatch, graph)
    _signup_login(owner, "optional-owner@example.com")
    _signup_login(other, "optional-other@example.com")
    group_id = owner.post("/groups", json={"name": "Optional Source Group"}).json()["id"]
    group_kb = _create_group_kb(owner, group_id, "Mandatory Group KB")
    owner_personal_kb = _create_personal_kb(owner, "Owner Optional KB")
    other_personal_kb = _create_personal_kb(other, "Other Private KB")
    _create_document(owner, title="Group Source", content="GroupBeta shared note.", kb_id=group_kb)
    owner_doc = _create_document(
        owner,
        title="Owner Optional Source",
        content="OwnerPrivateBeta optional personal note.",
        kb_id=owner_personal_kb,
    )
    conversation_id = owner.post(
        "/conversations", json={"title": "Optional group chat", "group_id": group_id}
    ).json()["id"]

    rejected = owner.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Tell me about my uploaded document.",
            "optional_personal_knowledge_base_ids": [other_personal_kb],
        },
    )
    accepted = owner.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Tell me about my uploaded document.",
            "optional_personal_knowledge_base_ids": [owner_personal_kb],
        },
    )

    assert rejected.status_code == 422
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["knowledge_base_selection"] == {"mode": "all", "knowledge_base_ids": []}
    assert payload["mandatory_group_knowledge_base_ids"] == [group_kb]
    assert payload["optional_personal_knowledge_base_ids"] == [owner_personal_kb]
    assert set(payload["resolved_knowledge_base_ids"]) == {group_kb, owner_personal_kb}
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {
        group_kb,
        owner_personal_kb,
    }
    assert owner_doc in {context["document_id"] for context in graph.calls[-1]["retrieved_context"]}


def test_group_chat_rejects_selected_group_kb_and_personal_chat_rejects_optional_personal(
    monkeypatch,
) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "group-source-validation@example.com")
    group_id = client.post("/groups", json={"name": "Validation Group"}).json()["id"]
    group_kb = _create_group_kb(client, group_id, "Validation Group KB")
    personal_kb = _create_personal_kb(client, "Validation Personal KB")
    group_conversation = client.post(
        "/conversations", json={"title": "Group validation", "group_id": group_id}
    ).json()["id"]
    personal_conversation = client.post(
        "/conversations", json={"title": "Personal validation"}
    ).json()["id"]

    selected_group = client.post(
        f"/conversations/{group_conversation}/runs",
        json={
            "message": "Tell me about docs.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [group_kb]},
        },
    )
    optional_in_personal = client.post(
        f"/conversations/{personal_conversation}/runs",
        json={
            "message": "Tell me about docs.",
            "optional_personal_knowledge_base_ids": [personal_kb],
        },
    )

    assert selected_group.status_code == 422
    assert optional_in_personal.status_code == 422


def test_group_kb_creator_loses_access_after_membership_removal(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    creator = _client(monkeypatch, graph)
    _signup_login(owner, "group-kb-auth-owner@example.com")
    creator_user_id = _signup_login(creator, "removed-group-kb-creator@example.com")
    group_id = owner.post("/groups", json={"name": "Revoked KB Group"}).json()["id"]
    assert (
        owner.post(
            f"/groups/{group_id}/members",
            json={"user_id": creator_user_id, "role": "editor"},
        ).status_code
        == 204
    )
    group_kb = _create_group_kb(creator, group_id, "Revoked Creator Group KB")
    group_document = _create_document(
        creator,
        title="Revoked Group Source",
        content="RevokedGroupGamma must not leak after membership removal.",
        kb_id=group_kb,
    )
    conversation_id = creator.post(
        "/conversations", json={"title": "Personal after group removal"}
    ).json()["id"]

    _remove_group_membership(group_id, creator_user_id)

    listed_ids = {kb["id"] for kb in creator.get("/knowledge-bases").json()}
    selected_response = creator.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Tell me about RevokedGroupGamma.",
            "knowledge_base_selection": {
                "mode": "selected",
                "knowledge_base_ids": [group_kb],
            },
        },
    )
    all_response = creator.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Tell me about RevokedGroupGamma."},
    )

    assert group_kb not in listed_ids
    assert creator.get(f"/knowledge-bases/{group_kb}").status_code == 404
    assert selected_response.status_code == 404
    assert all_response.status_code == 200
    assert group_document not in {
        context["document_id"] for context in graph.calls[-1]["retrieved_context"]
    }
    assert group_kb not in {
        citation["knowledge_base_id"] for citation in all_response.json()["citations"]
    }
