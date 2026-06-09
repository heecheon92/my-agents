"""Authenticated long-term memory API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from my_agents.api import create_app

from .conftest import verify_latest_auth_email


def _client(monkeypatch) -> TestClient:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(create_app())


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def test_memory_api_requires_opt_in_and_returns_provenance(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "memory-api@example.com")

    settings = client.get("/memories/settings")
    assert settings.status_code == 200
    assert settings.json()["enabled"] is False

    disabled_create = client.post(
        "/memories",
        json={"content": "Use concise answers", "category": "stable_preference"},
    )
    assert disabled_create.status_code == 409

    enabled = client.patch("/memories/settings", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    created = client.post(
        "/memories",
        json={"content": "Use concise answers", "category": "stable_preference"},
    )
    assert created.status_code == 201
    memory = created.json()
    assert memory["namespace"] == [user_id, "memories", "stable_preference"]
    assert memory["value"]["content"] == "Use concise answers"
    assert memory["provenance_type"] == "explicit_user"
    assert memory["sensitivity"] == "non_sensitive"

    listed = client.get("/memories")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [memory["id"]]


def test_memory_api_enforces_user_isolation_and_scrubbed_delete(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    _signup_login(owner, "memory-owner@example.com")
    assert owner.patch("/memories/settings", json={"enabled": True}).status_code == 200
    created = owner.post(
        "/memories",
        json={"content": "Owner memory", "category": "personal_fact"},
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    other = _client(monkeypatch)
    _signup_login(other, "memory-other@example.com")
    assert other.get("/memories").json() == []
    assert other.delete(f"/memories/{memory_id}").status_code == 404

    deactivated = owner.post(f"/memories/{memory_id}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"

    deleted = owner.delete(f"/memories/{memory_id}")
    assert deleted.status_code == 204
    assert owner.get("/memories").json() == []


def test_memory_api_rejects_client_asserted_provenance_and_value_payloads(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "memory-provenance@example.com")
    assert client.patch("/memories/settings", json={"enabled": True}).status_code == 200

    with_provenance = client.post(
        "/memories",
        json={
            "content": "Use concise answers",
            "category": "stable_preference",
            "source_document_id": "foreign-doc",
        },
    )
    assert with_provenance.status_code == 422

    with_value = client.post(
        "/memories",
        json={
            "content": "Use concise answers",
            "category": "stable_preference",
            "value": {"secret": "password swordfish"},
        },
    )
    assert with_value.status_code == 422

    misclassified_preference = client.post(
        "/memories",
        json={
            "content": "Project Phoenix uses FastAPI",
            "category": "stable_preference",
        },
    )
    assert misclassified_preference.status_code == 422


def test_memory_api_suggestion_confirm_reject_and_sensitive_guard(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "memory-suggestion@example.com")
    assert client.patch("/memories/settings", json={"enabled": True}).status_code == 200

    sensitive = client.post(
        "/memories/suggestions",
        json={"content": "My password is swordfish", "category": "personal_fact"},
    )
    assert sensitive.status_code == 422

    client_ttl = client.post(
        "/memories/suggestions",
        json={
            "content": "User prefers concise examples",
            "category": "stable_preference",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    assert client_ttl.status_code == 422

    suggestion = client.post(
        "/memories/suggestions",
        json={"content": "User prefers concise examples", "category": "stable_preference"},
    )
    assert suggestion.status_code == 201
    suggestion_payload = suggestion.json()
    assert suggestion_payload["status"] == "pending"
    assert suggestion_payload["value"]["content"] == "User prefers concise examples"
    assert client.get("/memories").json() == []

    confirmed = client.post(f"/memories/suggestions/{suggestion_payload['id']}/confirm")
    assert confirmed.status_code == 200
    memory = confirmed.json()
    assert memory["provenance_type"] == "assistant_suggested"
    assert memory["sensitivity"] == "non_sensitive"

    rejected = client.post(
        "/memories/suggestions",
        json={"content": "User uses pytest", "category": "project_context"},
    ).json()
    reject_response = client.post(f"/memories/suggestions/{rejected['id']}/reject")
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert client.get("/memories/suggestions").json() == []


def test_memory_api_confirm_after_disable_returns_conflict(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "memory-disable-confirm@example.com")
    assert client.patch("/memories/settings", json={"enabled": True}).status_code == 200
    suggestion = client.post(
        "/memories/suggestions",
        json={"content": "User prefers examples", "category": "stable_preference"},
    ).json()
    assert client.patch("/memories/settings", json={"enabled": False}).status_code == 200

    response = client.post(f"/memories/suggestions/{suggestion['id']}/confirm")

    assert response.status_code == 409
    assert response.json()["detail"] == "long-term memory is disabled"


def test_memory_api_rejects_blank_content(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "memory-blank@example.com")
    assert client.patch("/memories/settings", json={"enabled": True}).status_code == 200

    response = client.post("/memories", json={"content": "   ", "category": "stable_preference"})

    assert response.status_code == 422
