"""First-party auth API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.auth.models import AuthTokenModel
from my_agents.persistence.database import get_database_session

from .conftest import latest_auth_email_token, load_app, verify_latest_auth_email


def _client(monkeypatch) -> TestClient:  # noqa: ANN001 - pytest monkeypatch fixture
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    return TestClient(load_app())


def test_signup_verify_login_me_and_logout_revoke_owned_session(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    signup = client.post(
        "/auth/signup",
        json={"email": "User@Example.com", "password": "correct horse battery staple"},
    )

    assert signup.status_code == 201
    signup_payload = signup.json()
    assert signup_payload["user"]["email"] == "user@example.com"
    assert signup_payload["user"]["email_verified_at"] is None
    assert signup_payload["verification_email_sent"] is True
    assert "password" not in signup.text.lower()
    assert "password_hash" not in signup.text.lower()

    unverified_login = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "correct horse battery staple"},
    )

    assert unverified_login.status_code == 403
    assert "password" not in unverified_login.text.lower()

    verified = verify_latest_auth_email(client, "user@example.com")

    assert verified["email"] == "user@example.com"
    assert verified["email_verified_at"] is not None

    reused_token = client.post(
        "/auth/verify-email",
        json={"token": latest_auth_email_token("user@example.com", "email_verification")},
    )

    assert reused_token.status_code == 400

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
    assert login_payload["user"]["email_verified_at"] is not None
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
    verify_latest_auth_email(client, "login@example.com")

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


def test_password_reset_uses_local_email_and_revokes_old_session(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    old_password = "correct horse battery staple"
    new_password = "new correct horse battery staple"
    signup = client.post(
        "/auth/signup",
        json={"email": "reset@example.com", "password": old_password},
    )
    assert signup.status_code == 201
    verify_latest_auth_email(client, "reset@example.com")
    login = client.post(
        "/auth/login", json={"email": "reset@example.com", "password": old_password}
    )
    assert login.status_code == 200
    assert client.get("/auth/me").status_code == 200

    request_reset = client.post(
        "/auth/password-reset/request",
        json={"email": "reset@example.com"},
    )

    assert request_reset.status_code == 202
    assert request_reset.json() == {"status": "accepted"}

    reset_token = latest_auth_email_token("reset@example.com", "password_reset")
    confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": new_password},
    )

    assert confirm.status_code == 204
    assert client.get("/auth/me").status_code == 401
    assert (
        client.post(
            "/auth/login", json={"email": "reset@example.com", "password": old_password}
        ).status_code
        == 401
    )
    new_login = client.post(
        "/auth/login", json={"email": "reset@example.com", "password": new_password}
    )
    assert new_login.status_code == 200

    reused = client.post(
        "/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "another valid password"},
    )
    assert reused.status_code == 400


def test_password_reset_request_does_not_enumerate_unknown_accounts(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    response = client.post(
        "/auth/password-reset/request",
        json={"email": "missing@example.com"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}


def test_expired_auth_tokens_are_rejected(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    signup = client.post(
        "/auth/signup",
        json={"email": "expired@example.com", "password": "correct horse battery staple"},
    )
    assert signup.status_code == 201
    token = latest_auth_email_token("expired@example.com", "email_verification")

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        auth_token = db.scalar(
            select(AuthTokenModel).where(AuthTokenModel.purpose == "email_verification")
        )
        assert auth_token is not None
        auth_token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add(auth_token)
        db.commit()
    finally:
        session_generator.close()

    response = client.post("/auth/verify-email", json={"token": token})

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid or expired token"
