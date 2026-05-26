"""Frontend CORS contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from my_agents.api import create_app


def test_cors_preflight_allows_configured_frontend_origin(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv(
        "MY_AGENTS_CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    client = TestClient(create_app())

    response = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "X-CSRF-Token" in response.headers["access-control-allow-headers"]


def test_cors_preflight_rejects_unlisted_frontend_origin(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_CORS_ALLOWED_ORIGINS", "https://demo.example.com")
    client = TestClient(create_app())

    response = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert response.text == "Disallowed CORS origin"


def test_cors_headers_are_absent_when_frontend_origins_are_not_configured(monkeypatch) -> None:  # noqa: ANN001,E501
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.delenv("MY_AGENTS_CORS_ALLOWED_ORIGINS", raising=False)
    client = TestClient(create_app())

    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
