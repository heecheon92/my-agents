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


def _client_with_auth_attempt_limit(monkeypatch, max_attempts: int = 2) -> TestClient:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_MAX_ATTEMPTS", str(max_attempts))
    monkeypatch.setenv("MY_AGENTS_AUTH_ABUSE_WINDOW_SECONDS", "60")
    return _client(monkeypatch)


def test_login_cookie_is_secure_by_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.delenv("MY_AGENTS_SESSION_COOKIE_SECURE", raising=False)
    client = TestClient(load_app())

    client.post(
        "/auth/signup",
        json={"email": "secure-cookie@example.com", "password": "correct horse battery staple"},
    )
    verify_latest_auth_email(client, "secure-cookie@example.com")
    login = client.post(
        "/auth/login",
        json={
            "email": "secure-cookie@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert login.status_code == 200
    set_cookie = login.headers["set-cookie"].lower()
    assert "my_agents_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie


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
    assert client.get("/auth/me").status_code == 200

    logout = client.post("/auth/logout", headers={"X-CSRF-Token": login_payload["csrf_token"]})

    assert logout.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_logout_honors_configured_csrf_header(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_CSRF_HEADER_NAME", "X-Portfolio-CSRF")
    client = _client(monkeypatch)
    client.post(
        "/auth/signup",
        json={"email": "custom-csrf@example.com", "password": "correct horse battery staple"},
    )
    verify_latest_auth_email(client, "custom-csrf@example.com")
    login = client.post(
        "/auth/login",
        json={"email": "custom-csrf@example.com", "password": "correct horse battery staple"},
    )
    csrf_token = login.json()["csrf_token"]

    default_header_logout = client.post("/auth/logout", headers={"X-CSRF-Token": csrf_token})

    assert default_header_logout.status_code == 403
    assert client.get("/auth/me").status_code == 200
    configured_header_logout = client.post(
        "/auth/logout",
        headers={"X-Portfolio-CSRF": csrf_token},
    )
    assert configured_header_logout.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_signup_rejects_duplicate_email(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    payload = {"email": "duplicate@example.com", "password": "correct horse battery staple"}

    assert client.post("/auth/signup", json=payload).status_code == 201
    duplicate = client.post("/auth/signup", json=payload)

    assert duplicate.status_code == 409
    assert "password" not in duplicate.text.lower()


def test_dev_auth_outbox_is_disabled_by_default(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    response = client.get("/auth/dev/outbox")

    assert response.status_code == 404


def test_dev_auth_outbox_exposes_local_tokens_only_when_enabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED", "true")
    client = _client(monkeypatch)

    signup = client.post(
        "/auth/signup",
        json={"email": "outbox@example.com", "password": "correct horse battery staple"},
    )
    outbox = client.get("/auth/dev/outbox")

    assert signup.status_code == 201
    assert outbox.status_code == 200
    payload = outbox.json()
    assert payload[-1]["recipient_email"] == "outbox@example.com"
    assert payload[-1]["purpose"] == "email_verification"
    assert payload[-1]["token"] == latest_auth_email_token(
        "outbox@example.com", "email_verification"
    )


def test_signup_attempts_are_rate_limited(monkeypatch) -> None:  # noqa: ANN001
    client = _client_with_auth_attempt_limit(monkeypatch)
    payload = {"email": "limited-signup@example.com", "password": "correct horse battery staple"}

    assert client.post("/auth/signup", json=payload).status_code == 201
    assert client.post("/auth/signup", json=payload).status_code == 409
    limited = client.post("/auth/signup", json=payload)

    assert limited.status_code == 429
    assert limited.json()["detail"] == "too many auth attempts"


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


def test_repeated_bad_logins_are_rate_limited(monkeypatch) -> None:  # noqa: ANN001
    client = _client_with_auth_attempt_limit(monkeypatch)
    client.post(
        "/auth/signup",
        json={"email": "limited-login@example.com", "password": "correct horse battery staple"},
    )
    verify_latest_auth_email(client, "limited-login@example.com")
    bad_payload = {"email": "limited-login@example.com", "password": "wrong"}

    assert client.post("/auth/login", json=bad_payload).status_code == 401
    assert client.post("/auth/login", json=bad_payload).status_code == 401
    limited = client.post("/auth/login", json=bad_payload)

    assert limited.status_code == 429
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


def test_repeated_password_reset_requests_are_rate_limited(monkeypatch) -> None:  # noqa: ANN001
    client = _client_with_auth_attempt_limit(monkeypatch)
    client.post(
        "/auth/signup",
        json={"email": "limited-reset@example.com", "password": "correct horse battery staple"},
    )
    payload = {"email": "limited-reset@example.com"}

    assert client.post("/auth/password-reset/request", json=payload).status_code == 202
    assert client.post("/auth/password-reset/request", json=payload).status_code == 202
    limited = client.post("/auth/password-reset/request", json=payload)

    assert limited.status_code == 429
    assert limited.json()["detail"] == "too many auth attempts"


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


def test_invalid_email_verification_tokens_are_rate_limited(monkeypatch) -> None:  # noqa: ANN001
    client = _client_with_auth_attempt_limit(monkeypatch)
    payload = {"token": "not-a-real-token"}

    assert client.post("/auth/verify-email", json=payload).status_code == 400
    assert client.post("/auth/verify-email", json=payload).status_code == 400
    limited = client.post("/auth/verify-email", json=payload)

    assert limited.status_code == 429
    assert limited.json()["detail"] == "too many auth attempts"


def test_invalid_password_reset_tokens_are_rate_limited(monkeypatch) -> None:  # noqa: ANN001
    client = _client_with_auth_attempt_limit(monkeypatch)
    payload = {"token": "not-a-real-token", "new_password": "new correct horse battery staple"}

    assert client.post("/auth/password-reset/confirm", json=payload).status_code == 400
    assert client.post("/auth/password-reset/confirm", json=payload).status_code == 400
    limited = client.post("/auth/password-reset/confirm", json=payload)

    assert limited.status_code == 429
    assert limited.json()["detail"] == "too many auth attempts"
