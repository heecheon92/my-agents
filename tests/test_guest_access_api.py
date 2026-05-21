"""Provider-free public-demo guest access tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.auth.models import AuthTokenModel, GuestAccessCodeModel, SessionModel, UserModel
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class GuestSpyGraph:
    """Small deterministic graph for guest run limit tests."""

    def invoke(self, input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        return {
            "reply": f"guest saw {len(input['messages'])} messages",
            "route": RouteDecision(label="general_assistant", explanation="guest spy"),
        }


def _client(monkeypatch, *, guest_enabled: bool = True) -> TestClient:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true" if guest_enabled else "false")
    app = create_app()
    app.dependency_overrides[get_graph_runner] = lambda: GuestSpyGraph()
    return TestClient(app)


def _guest_login(client: TestClient) -> dict[str, Any]:
    code_response = client.post("/auth/guest/request")
    assert code_response.status_code == 200
    code = code_response.json()["code"]
    login = client.post("/auth/guest/login", json={"code": code})
    assert login.status_code == 200
    return login.json()


def _signup_login(client: TestClient, email: str) -> None:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200


def _first_row(model):  # noqa: ANN001 - SQLAlchemy model class
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return db.scalars(select(model)).first()
    finally:
        session_generator.close()


def test_guest_access_is_disabled_by_default(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, guest_enabled=False)

    code_response = client.post("/auth/guest/request")
    login_response = client.post("/auth/guest/login", json={"code": "not-a-code"})

    assert code_response.status_code == 403
    assert code_response.json()["detail"] == "guest access disabled"
    assert login_response.status_code == 403
    assert login_response.json()["detail"] == "guest access disabled"


def test_guest_code_redeems_once_and_me_uses_guest_session(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    code = client.post("/auth/guest/request").json()["code"]

    first_login = client.post("/auth/guest/login", json={"code": code})
    reused_login = client.post("/auth/guest/login", json={"code": code})
    me = client.get("/auth/me")

    assert first_login.status_code == 200
    assert "my_agents_session=" in first_login.headers["set-cookie"].lower()
    assert first_login.json()["user"]["email"] is None
    assert first_login.json()["user"]["is_guest"] is True
    assert first_login.json()["user"]["guest_expires_at"] is not None
    assert first_login.json()["csrf_token"]
    assert reused_login.status_code == 400
    assert reused_login.json()["detail"] == "invalid or expired guest code"
    assert me.status_code == 200
    assert me.json()["is_guest"] is True
    assert me.json()["email"] is None


def test_expired_guest_code_and_session_are_rejected(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    code = client.post("/auth/guest/request").json()["code"]

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        guest_code = db.scalar(select(GuestAccessCodeModel))
        assert guest_code is not None
        guest_code.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add(guest_code)
        db.commit()
    finally:
        session_generator.close()

    expired_login = client.post("/auth/guest/login", json={"code": code})
    assert expired_login.status_code == 400
    assert expired_login.json()["detail"] == "invalid or expired guest code"

    _guest_login(client)
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        guest_user = db.scalar(select(UserModel).where(UserModel.account_type == "guest"))
        session = db.scalar(select(SessionModel).where(SessionModel.user_id == guest_user.id))
        assert guest_user is not None
        assert session is not None
        guest_user.guest_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add_all([guest_user, session])
        db.commit()
    finally:
        session_generator.close()

    me = client.get("/auth/me")
    assert me.status_code == 401


def test_guest_can_create_only_one_conversation(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _guest_login(client)

    first = client.post("/conversations", json={"title": "Guest chat"})
    second = client.post("/conversations", json={"title": "Another guest chat"})

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["detail"] == "guest conversation limit reached"


def test_guest_prompt_cap_applies_to_chat_runs(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _guest_login(client)
    conversation_id = client.post("/conversations", json={"title": "Guest prompts"}).json()["id"]

    responses = [
        client.post(f"/conversations/{conversation_id}/runs", json={"message": f"Prompt {index}"})
        for index in range(5)
    ]
    limited = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Prompt 6"},
    )

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200]
    assert limited.status_code == 429
    assert limited.json()["detail"] == "guest prompt limit reached"


def test_guest_document_cap_applies_to_creates_and_uploads(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _guest_login(client)

    responses = [
        client.post("/documents", json={"title": f"Guest doc {index}", "content": "demo"})
        for index in range(3)
    ]
    limited_upload = client.post(
        "/documents/upload",
        data={"title": "Too many"},
        files={"file": ("not-a.pdf", b"not a pdf", "application/pdf")},
    )

    assert [response.status_code for response in responses] == [201, 201, 201]
    assert limited_upload.status_code == 429
    assert limited_upload.json()["detail"] == "guest document limit reached"


def test_guest_cannot_use_password_reset_or_email_lifecycle(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_AUTH_DEV_OUTBOX_ENABLED", "true")
    client = _client(monkeypatch)
    _guest_login(client)
    guest_user = _first_row(UserModel)
    assert guest_user is not None
    assert guest_user.account_type == "guest"

    reset = client.post("/auth/password-reset/request", json={"email": guest_user.email})
    verify = client.post("/auth/verify-email", json={"token": "not-a-guest-token"})
    outbox = client.get("/auth/dev/outbox")
    token = _first_row(AuthTokenModel)

    assert reset.status_code == 202
    assert verify.status_code == 400
    assert outbox.status_code == 403
    assert outbox.json()["detail"] == "not allowed"
    assert token is None


def test_normal_auth_remains_unchanged_with_guest_enabled(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    _signup_login(client, "registered-with-guest@example.com")
    me = client.get("/auth/me")

    assert me.status_code == 200
    assert me.json()["email"] == "registered-with-guest@example.com"
    assert me.json()["is_guest"] is False
    assert me.json()["guest_expires_at"] is None
