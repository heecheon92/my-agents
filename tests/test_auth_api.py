"""First-party auth API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.auth.dependencies import get_configured_auth_email_sender
from my_agents.auth.email import get_local_auth_email_outbox
from my_agents.auth.models import AuthTokenModel, GuestAccessCodeModel, UserModel
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings
from scripts.approve_account_signup import (
    approve_account_signup,
    send_account_verification_email,
)
from scripts.resend_account_verification import resend_account_verification

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


def test_signup_is_enabled_by_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("MY_AGENTS_AUTH_SIGNUP_ENABLED", raising=False)
    client = _client(monkeypatch)

    response = client.post(
        "/auth/signup",
        json={
            "email": "default-signup@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 201
    assert response.json()["user"]["email"] == "default-signup@example.com"


def test_signup_auto_approval_defaults_to_pending_account(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", raising=False)
    get_settings.cache_clear()
    client = _client(monkeypatch)

    response = client.post(
        "/auth/signup",
        json={
            "email": "pending-default@example.com",
            "password": "correct horse battery staple",
        },
    )
    login = client.post(
        "/auth/login",
        json={
            "email": "pending-default@example.com",
            "password": "correct horse battery staple",
        },
    )
    reset = client.post(
        "/auth/password-reset/request",
        json={"email": "pending-default@example.com"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["verification_email_sent"] is False
    assert payload["approval_required"] is True
    assert payload["user"]["approval_status"] == "pending"
    assert get_local_auth_email_outbox().messages() == ()
    assert login.status_code == 403
    assert login.json()["detail"] == "account approval pending"
    assert reset.status_code == 202
    assert get_local_auth_email_outbox().messages() == ()


def test_manual_account_approval_issues_verification_token_and_allows_login(
    monkeypatch,
    tmp_path,
) -> None:  # noqa: ANN001
    monkeypatch.setenv(
        "MY_AGENTS_DATABASE_URL",
        f"sqlite+pysqlite:///{tmp_path / 'manual-account-approval.sqlite3'}",
    )
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    monkeypatch.delenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", raising=False)
    get_settings.cache_clear()
    client = _client(monkeypatch)

    signup = client.post(
        "/auth/signup",
        json={
            "email": "manual-approval@example.com",
            "password": "correct horse battery staple",
        },
    )
    result = approve_account_signup(
        settings=Settings(_env_file=None),
        email="manual-approval@example.com",
    )
    send_account_verification_email(
        settings=Settings(_env_file=None),
        result=result,
        language="ko",
    )
    verified = client.post("/auth/verify-email", json={"token": result.verification_token})
    login = client.post(
        "/auth/login",
        json={
            "email": "manual-approval@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert signup.status_code == 201
    assert result.email == "manual-approval@example.com"
    assert result.verification_token
    assert get_local_auth_email_outbox().messages()[-1].purpose == "email_verification"
    assert verified.status_code == 200
    assert login.status_code == 200
    assert login.json()["user"]["approval_status"] == "approved"


def test_manual_account_approval_can_mark_email_verified_without_token(
    monkeypatch,
    tmp_path,
) -> None:  # noqa: ANN001
    monkeypatch.setenv(
        "MY_AGENTS_DATABASE_URL",
        f"sqlite+pysqlite:///{tmp_path / 'manual-account-approval-verified.sqlite3'}",
    )
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    monkeypatch.delenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", raising=False)
    get_settings.cache_clear()
    client = _client(monkeypatch)

    signup = client.post(
        "/auth/signup",
        json={
            "email": "manual-mark-verified@example.com",
            "password": "correct horse battery staple",
        },
    )
    result = approve_account_signup(
        settings=Settings(_env_file=None),
        email="manual-mark-verified@example.com",
        mark_email_verified=True,
    )
    login = client.post(
        "/auth/login",
        json={
            "email": "manual-mark-verified@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert signup.status_code == 201
    assert result.email == "manual-mark-verified@example.com"
    assert result.verification_token is None
    assert result.email_marked_verified is True
    assert result.was_email_already_verified is False
    assert login.status_code == 200
    assert login.json()["user"]["approval_status"] == "approved"
    assert login.json()["user"]["email_verified_at"] is not None


def test_resend_account_verification_recovers_expired_signup_token(
    monkeypatch,
    tmp_path,
) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'expired-signup-token.sqlite3'}"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", database_url)
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", "true")
    get_settings.cache_clear()
    client = _client(monkeypatch)

    signup = client.post(
        "/auth/signup",
        json={
            "email": "expired-token@example.com",
            "password": "correct horse battery staple",
        },
    )
    expired_token = latest_auth_email_token("expired-token@example.com", "email_verification")

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        auth_token = db.scalar(select(AuthTokenModel))
        assert auth_token is not None
        auth_token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add(auth_token)
        db.commit()
    finally:
        session_generator.close()

    expired_verify = client.post("/auth/verify-email", json={"token": expired_token})
    duplicate_signup = client.post(
        "/auth/signup",
        json={
            "email": "expired-token@example.com",
            "password": "correct horse battery staple",
        },
    )
    result = resend_account_verification(
        settings=Settings(_env_file=None),
        email="expired-token@example.com",
    )
    verified = client.post("/auth/verify-email", json={"token": result.verification_token})
    login = client.post(
        "/auth/login",
        json={
            "email": "expired-token@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert signup.status_code == 201
    assert expired_verify.status_code == 400
    assert duplicate_signup.status_code == 409
    assert result.email == "expired-token@example.com"
    assert result.verification_token != expired_token
    assert verified.status_code == 200
    assert login.status_code == 200


def test_guest_request_manual_default_records_without_code_or_email(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    monkeypatch.delenv("MY_AGENTS_GUEST_CODE_AUTO_APPROVAL", raising=False)
    get_settings.cache_clear()
    client = _client(monkeypatch)

    response = client.post("/auth/guest/request", json={"email": "guest-manual@example.com"})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert get_local_auth_email_outbox().messages() == ()

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        guest_code = db.scalar(select(GuestAccessCodeModel))
    finally:
        session_generator.close()

    assert guest_code is None


def test_guest_auto_approval_emails_code_and_allows_guest_login(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_CODE_AUTO_APPROVAL", "true")
    get_settings.cache_clear()
    client = _client(monkeypatch)

    response = client.post(
        "/auth/guest/request",
        json={"email": "guest-auto@example.com", "language": "en"},
    )
    messages = get_local_auth_email_outbox().messages()
    code = messages[-1].token
    login = client.post("/auth/guest/login", json={"code": code})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert messages[-1].recipient_email == "guest-auto@example.com"
    assert messages[-1].purpose == "guest_access_code"
    assert login.status_code == 200
    assert login.json()["user"]["is_guest"] is True


def test_guest_auto_approval_email_failure_does_not_persist_code(monkeypatch) -> None:  # noqa: ANN001
    class FailingGuestEmailSender:
        def send_email_verification(self, **kwargs) -> None:  # noqa: ANN003
            return None

        def send_password_reset(self, **kwargs) -> None:  # noqa: ANN003
            return None

        def send_guest_access_code(self, **kwargs) -> None:  # noqa: ANN003
            raise RuntimeError("email provider failed")

    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_CODE_AUTO_APPROVAL", "true")
    get_settings.cache_clear()
    app = load_app()
    app.dependency_overrides[get_configured_auth_email_sender] = lambda: FailingGuestEmailSender()
    client = TestClient(app)
    try:
        response = client.post(
            "/auth/guest/request",
            json={"email": "guest-failure@example.com"},
        )
    finally:
        app.dependency_overrides.pop(get_configured_auth_email_sender, None)

    assert response.status_code == 503
    assert response.json()["detail"] == "guest access temporarily unavailable"

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        guest_code = db.scalar(select(GuestAccessCodeModel))
    finally:
        session_generator.close()

    assert guest_code is None


def test_disabled_signup_does_not_create_user_token_or_email(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_AUTH_SIGNUP_ENABLED", "false")
    client = _client(monkeypatch)

    response = client.post(
        "/auth/signup",
        json={
            "email": "disabled-signup@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "signup disabled"
    assert "password" not in response.text.lower()
    assert get_local_auth_email_outbox().messages() == ()

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        user = db.scalar(select(UserModel).where(UserModel.email == "disabled-signup@example.com"))
        auth_token = db.scalar(select(AuthTokenModel))
    finally:
        session_generator.close()

    assert user is None
    assert auth_token is None
    assert client.get("/auth/me").status_code == 401


def test_disabled_signup_does_not_block_existing_login(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    signup = client.post(
        "/auth/signup",
        json={
            "email": "existing-login@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert signup.status_code == 201
    verify_latest_auth_email(client, "existing-login@example.com")

    monkeypatch.setenv("MY_AGENTS_AUTH_SIGNUP_ENABLED", "false")
    get_settings.cache_clear()

    blocked_signup = client.post(
        "/auth/signup",
        json={
            "email": "new-disabled-signup@example.com",
            "password": "correct horse battery staple",
        },
    )
    login = client.post(
        "/auth/login",
        json={
            "email": "existing-login@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert blocked_signup.status_code == 403
    assert login.status_code == 200
    assert client.get("/auth/me").status_code == 200


def test_logout_honors_configured_csrf_header(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_CSRF_HEADER_NAME", "X-Demo-CSRF")
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
        headers={"X-Demo-CSRF": csrf_token},
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
