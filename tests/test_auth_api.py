"""First-party auth API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import load_app


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(load_app())


def test_signup_login_me_and_logout_revoke_owned_session(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    signup = client.post(
        "/auth/signup",
        json={"email": "User@Example.com", "password": "correct horse battery staple"},
    )

    assert signup.status_code == 201
    signup_payload = signup.json()
    assert signup_payload["email"] == "user@example.com"
    assert "password" not in signup_payload
    assert "password_hash" not in signup_payload

    login = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "correct horse battery staple"},
    )

    assert login.status_code == 200
    login_payload = login.json()
    set_cookie = login.headers["set-cookie"].lower()
    assert "my_agents_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert login_payload["user"]["email"] == "user@example.com"
    assert login_payload["csrf_token"]
    assert "password" not in str(login_payload).lower()
    assert "password_hash" not in str(login_payload).lower()

    me = client.get("/auth/me")

    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"

    logout_without_csrf = client.post("/auth/logout")

    assert logout_without_csrf.status_code == 403

    logout = client.post("/auth/logout", headers={"X-CSRF-Token": login_payload["csrf_token"]})

    assert logout.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_signup_rejects_duplicate_email(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    payload = {"email": "duplicate@example.com", "password": "correct horse battery staple"}

    assert client.post("/auth/signup", json=payload).status_code == 201
    duplicate = client.post("/auth/signup", json=payload)

    assert duplicate.status_code == 409
    assert "password" not in duplicate.text.lower()


def test_login_rejects_invalid_credentials_without_session(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    client.post(
        "/auth/signup",
        json={"email": "login@example.com", "password": "correct horse battery staple"},
    )

    response = client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "wrong"},
    )

    assert response.status_code == 401
    assert client.get("/auth/me").status_code == 401


def test_protected_me_requires_session(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    response = client.get("/auth/me")

    assert response.status_code == 401
