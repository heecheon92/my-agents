"""Knowledge-base ingestion and deterministic extraction tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import load_app


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(load_app())


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["id"]


def test_personal_knowledge_base_document_ingestion_creates_extraction_artifacts(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "kb-owner@example.com")

    kb = client.post("/knowledge-bases", json={"name": "Personal KB", "scope": "personal"})
    assert kb.status_code == 201
    kb_id = kb.json()["id"]

    document = client.post(
        "/documents",
        json={
            "title": "Agent Notes",
            "content": "OpenAI builds agents with LangGraph.\n\nLangGraph helps Heecheon Park.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert document.json()["knowledge_base_id"] == kb_id

    ingest = client.post(f"/documents/{document.json()['id']}/ingest")

    assert ingest.status_code == 200
    payload = ingest.json()
    assert payload["status"] == "completed"
    assert payload["chunk_count"] == 2
    assert payload["entity_count"] >= 3
    assert payload["relationship_count"] >= 1

    runs = client.get(f"/documents/{document.json()['id']}/extraction-runs")
    assert runs.status_code == 200
    assert runs.json()[0]["id"] == payload["id"]


def test_group_knowledge_base_requires_group_membership(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "group-kb-owner@example.com")
    _signup_login(outsider, "group-kb-outsider@example.com")
    group_id = owner.post("/groups", json={"name": "KB Group"}).json()["id"]

    kb = owner.post(
        "/knowledge-bases",
        json={"name": "Group KB", "scope": "group", "group_id": group_id},
    )
    assert kb.status_code == 201
    assert kb.json()["group_id"] == group_id

    denied = outsider.post(
        "/knowledge-bases",
        json={"name": "Denied", "scope": "group", "group_id": group_id},
    )
    assert denied.status_code == 403
    assert outsider.get("/knowledge-bases").json() == []
