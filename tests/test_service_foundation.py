"""Service-foundation scaffold tests for the product chat roadmap."""

from __future__ import annotations

from fastapi.testclient import TestClient

from my_agents.api import create_app
from my_agents.auth.contracts import Principal
from my_agents.permissions.contracts import DocumentOperation
from my_agents.persistence import get_persistence_config
from my_agents.settings import Settings


def test_create_app_still_exposes_health_and_legacy_assistant_routes() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/health" in paths
    assert "/assistant/chat" in paths

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "my-agents",
        "version": "0.1.0",
        "frontend_config": {"documents": {"upload_concurrency": 3}},
    }


def test_health_exposes_env_configured_document_upload_concurrency(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_DOCUMENT_UPLOAD_CONCURRENCY", "5")
    app = create_app()

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["frontend_config"]["documents"]["upload_concurrency"] == 5


def test_persistence_config_normalizes_settings() -> None:
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_DATABASE_URL="postgresql+psycopg://app:pw@localhost/app",
        MY_AGENTS_TEST_DATABASE_URL="postgresql+psycopg://app:pw@localhost/test_app",
    )

    config = get_persistence_config(settings)

    assert config.database_url == "postgresql+psycopg://app:pw@localhost/app"
    assert config.test_database_url == "postgresql+psycopg://app:pw@localhost/test_app"
    assert config.auto_create_tables is False
    assert config.has_external_test_database is True


def test_auth_and_permission_contracts_are_explicit() -> None:
    principal = Principal(user_id="user_123", session_id="session_123")

    assert principal.user_id == "user_123"
    assert principal.session_id == "session_123"
    assert {operation.value for operation in DocumentOperation} == {
        "read",
        "write",
        "manage_permissions",
        "delete",
        "ingest",
        "retrieve",
        "cite",
    }
