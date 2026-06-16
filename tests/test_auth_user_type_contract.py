"""User-type capability and script-only mutation contract tests."""

from __future__ import annotations

import importlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.auth.models import UserModel
from my_agents.auth.schemas import AccountNicknameUpdateRequest, UserResponse
from my_agents.persistence.database import get_database_session

from .conftest import verify_latest_auth_email


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    return TestClient(create_app())


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post(
        "/auth/signup",
        json={"email": email, "nickname": "Test User", "password": password},
    )
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _set_user_type(user_id: str, user_type: str) -> None:
    assert hasattr(UserModel, "user_type"), "users.user_type column must exist"
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        user = db.scalar(select(UserModel).where(UserModel.id == user_id))
        assert user is not None
        user.user_type = user_type
        db.commit()
    finally:
        session_generator.close()


def test_user_type_model_principal_and_response_contract() -> None:
    auth_models = importlib.import_module("my_agents.auth.models")
    auth_contracts = importlib.import_module("my_agents.auth.contracts")
    UserType = getattr(auth_models, "UserType", None)
    assert UserType is not None, "auth model must expose UserType"
    assert {member.value for member in UserType} == {"normal", "root", "system"}

    Principal = auth_contracts.Principal
    normal = Principal(user_id="user-1", session_id="session-1", user_type="normal")
    root = Principal(user_id="user-2", session_id="session-2", user_type="root")
    system = Principal(user_id="user-3", session_id="session-3", user_type="system")
    guest = Principal(
        user_id="guest-1",
        session_id="session-4",
        is_guest=True,
        user_type="normal",
    )

    assert normal.can_manage_system_knowledge is False
    assert guest.can_manage_system_knowledge is False
    assert root.can_manage_system_knowledge is True
    assert system.can_manage_system_knowledge is True

    response = UserResponse.model_validate(
        {
            "id": "user-1",
            "email": "user@example.com",
            "nickname": "Test User",
            "email_verified_at": datetime.now(UTC),
            "approval_status": "approved",
            "is_guest": False,
            "guest_expires_at": None,
        }
    )
    assert response.user_type is None
    assert response.can_manage_system_knowledge is None

    manager_response = UserResponse.model_validate(
        {
            "id": "user-2",
            "email": "manager@example.com",
            "nickname": "Manager",
            "email_verified_at": datetime.now(UTC),
            "approval_status": "approved",
            "is_guest": False,
            "guest_expires_at": None,
            "user_type": "root",
            "can_manage_system_knowledge": True,
        }
    )
    assert manager_response.user_type == "root"
    assert manager_response.can_manage_system_knowledge is True


def test_current_user_omits_normal_type_and_exposes_privileged_capability(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "user-type-me@example.com")

    normal_me = client.get("/auth/me")

    assert normal_me.status_code == 200
    normal_payload = normal_me.json()
    assert "user_type" not in normal_payload
    assert "can_manage_system_knowledge" not in normal_payload
    assert "password" not in normal_me.text.lower()
    assert "session" not in normal_me.text.lower()

    _set_user_type(user_id, "root")

    root_me = client.get("/auth/me")

    assert root_me.status_code == 200
    root_payload = root_me.json()
    assert root_payload["user_type"] == "root"
    assert root_payload["can_manage_system_knowledge"] is True
    assert "password" not in root_me.text.lower()
    assert "session" not in root_me.text.lower()


def test_user_type_is_not_mutable_through_profile_request_schema() -> None:
    with pytest.raises(ValidationError):
        AccountNicknameUpdateRequest.model_validate(
            {
                "current_password": "correct horse battery staple",
                "nickname": "Root Please",
                "user_type": "root",
            }
        )


def test_set_user_type_script_dry_run_and_guest_refusal(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    database_path = tmp_path / "set-user-type.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path}"
    env_file = tmp_path / "set-user-type.env"
    env_file.write_text(
        f"MY_AGENTS_DATABASE_URL={database_url}\nMY_AGENTS_RESPONSE_MODE=deterministic\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", database_url)
    client = _client(monkeypatch)
    registered_user_id = _signup_login(client, "promote-me@example.com")

    script_path = Path("scripts/set_user_type.py")
    assert script_path.exists(), "operator-only user type mutation script must exist"

    dry_run = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--env-file",
            str(env_file),
            "--email",
            "promote-me@example.com",
            "--user-type",
            "root",
            "--dry-run",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert dry_run.returncode == 0
    assert "promote-me@example.com" in dry_run.stdout
    assert "normal" in dry_run.stdout
    assert "root" in dry_run.stdout
    assert "password" not in dry_run.stdout.lower()
    assert "token" not in dry_run.stdout.lower()

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        user = db.scalar(select(UserModel).where(UserModel.id == registered_user_id))
        assert user is not None
        assert user.user_type == "normal"
    finally:
        session_generator.close()

    missing_identifier = subprocess.run(
        [sys.executable, str(script_path), "--env-file", str(env_file), "--user-type", "root"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert missing_identifier.returncode != 0

    invalid_type = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--env-file",
            str(env_file),
            "--email",
            "promote-me@example.com",
            "--user-type",
            "admin",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert invalid_type.returncode != 0
