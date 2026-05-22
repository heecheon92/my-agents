"""KB-nested document route and chat source-boundary regressions."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.knowledge.models import DocumentModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class KbSpyGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict) -> dict:  # noqa: A002
        self.calls.append(input)
        return {
            "reply": "kb graph response",
            "route": RouteDecision(label="general_assistant", explanation="kb spy"),
        }


def _client(monkeypatch, graph: KbSpyGraph | None = None) -> TestClient:  # noqa: ANN001
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


def _create_kb(client: TestClient, name: str) -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _document_rows(document_id: str) -> list[DocumentModel]:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return db.scalars(select(DocumentModel).where(DocumentModel.id == document_id)).all()
    finally:
        session_generator.close()


def test_kb_nested_document_routes_enforce_no_null_writes_and_wrong_kb_404(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "kb-nested@example.com")
    kb_id = _create_kb(client, "Primary KB")
    other_kb_id = _create_kb(client, "Other KB")

    detail = client.get(f"/knowledge-bases/{kb_id}")
    created = client.post(
        f"/knowledge-bases/{kb_id}/documents",
        json={"title": "Nested Text", "content": "Nested Alpha uses LangGraph."},
    )
    uploaded = client.post(
        f"/knowledge-bases/{kb_id}/documents/upload",
        data={"title": "Nested Upload"},
        files={"file": ("nested.txt", b"Nested upload mentions FastAPI.", "text/plain")},
    )
    listed = client.get(f"/knowledge-bases/{kb_id}/documents")
    wrong_kb_ingest = client.post(
        f"/knowledge-bases/{other_kb_id}/documents/{created.json()['id']}/ingest"
    )
    ingest = client.post(f"/knowledge-bases/{kb_id}/documents/{created.json()['id']}/ingest")
    async_ingest = client.post(
        f"/knowledge-bases/{kb_id}/documents/{uploaded.json()['id']}/ingest/async"
    )

    assert detail.status_code == 200
    assert created.status_code == 201
    assert uploaded.status_code == 201
    assert created.json()["knowledge_base_id"] == kb_id
    assert uploaded.json()["knowledge_base_id"] == kb_id
    assert {document["id"] for document in listed.json()} == {
        created.json()["id"],
        uploaded.json()["id"],
    }
    assert wrong_kb_ingest.status_code == 404
    assert ingest.status_code == 200
    assert async_ingest.status_code == 202
    persisted = _document_rows(created.json()["id"])
    assert persisted[0].knowledge_base_id == kb_id


def test_legacy_document_writes_require_authorized_kb(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "legacy-owner@example.com")
    _signup_login(outsider, "legacy-outsider@example.com")
    kb_id = _create_kb(owner, "Legacy KB")

    missing_json = owner.post("/documents", json={"title": "Missing KB", "content": "no"})
    missing_upload = owner.post(
        "/documents/upload",
        data={"title": "Missing KB"},
        files={"file": ("doc.txt", b"text", "text/plain")},
    )
    nonexistent = owner.post(
        "/documents",
        json={"title": "Bad KB", "content": "no", "knowledge_base_id": "missing"},
    )
    unauthorized = outsider.post(
        "/documents",
        json={"title": "Other KB", "content": "no", "knowledge_base_id": kb_id},
    )
    legacy_success = owner.post(
        "/documents",
        json={"title": "Legacy OK", "content": "yes", "knowledge_base_id": kb_id},
    )

    assert missing_json.status_code == 422
    assert missing_upload.status_code == 422
    assert nonexistent.status_code == 404
    assert unauthorized.status_code == 404
    assert legacy_success.status_code == 201
    assert _document_rows(legacy_success.json()["id"])[0].knowledge_base_id == kb_id


def test_selected_kb_chat_scope_filters_retrieval_and_metadata(monkeypatch) -> None:  # noqa: ANN001
    graph = KbSpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "kb-chat-owner@example.com")
    _signup_login(outsider, "kb-chat-outsider@example.com")
    kb_a = _create_kb(owner, "KB A")
    kb_b = _create_kb(owner, "KB B")
    doc_a = owner.post(
        f"/knowledge-bases/{kb_a}/documents",
        json={"title": "Alpha KB", "content": "AlphaOnly knowledge is in selected A."},
    )
    doc_b = owner.post(
        f"/knowledge-bases/{kb_b}/documents",
        json={"title": "Beta KB", "content": "BetaOnly knowledge is in selected B."},
    )
    assert (
        owner.post(f"/knowledge-bases/{kb_a}/documents/{doc_a.json()['id']}/ingest").status_code
        == 200
    )
    assert (
        owner.post(f"/knowledge-bases/{kb_b}/documents/{doc_b.json()['id']}/ingest").status_code
        == 200
    )
    conversation_id = owner.post("/conversations", json={"title": "KB chat"}).json()["id"]

    invalid_empty = owner.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "uploaded document", "knowledge_base_selection": {"mode": "selected"}},
    )
    invalid_all = owner.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "uploaded document",
            "knowledge_base_selection": {"mode": "all", "knowledge_base_ids": [kb_a]},
        },
    )
    outsider_conversation_id = outsider.post("/conversations", json={"title": "Outsider"}).json()[
        "id"
    ]
    unauthorized = outsider.post(
        f"/conversations/{outsider_conversation_id}/runs",
        json={
            "message": "uploaded document",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_a]},
        },
    )
    selected_b = owner.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "What does my uploaded document say about BetaOnly?",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_b]},
        },
    )

    assert invalid_empty.status_code == 422
    assert invalid_all.status_code == 422
    assert unauthorized.status_code == 404
    assert selected_b.status_code == 200
    payload = selected_b.json()
    assert payload["knowledge_base_selection"] == {
        "mode": "selected",
        "knowledge_base_ids": [kb_b],
    }
    assert payload["resolved_knowledge_base_count"] == 1
    assert payload["citations"]
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {kb_b}
    assert graph.calls[-1]["retrieved_context"][0]["document_id"] == doc_b.json()["id"]
    detail = owner.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}")
    events = owner.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}/events")
    assert detail.json()["knowledge_base_selection"] == payload["knowledge_base_selection"]
    assert (
        events.json()[0]["payload"]["knowledge_base_selection"]
        == payload["knowledge_base_selection"]
    )
