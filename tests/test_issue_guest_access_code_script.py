"""Manual guest access code issue script tests."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from my_agents.auth.models import GuestAccessCodeModel, GuestAccessRequestModel
from my_agents.auth.service import AuthService
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings
from scripts.issue_guest_access_code import (
    GuestCodeIssueResult,
    issue_guest_access_code,
    main,
    resolve_env_file,
    send_guest_access_code_email,
)


def _settings(monkeypatch, database_url: str) -> Settings:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", database_url)
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    return Settings(_env_file=None)


def test_issue_guest_access_code_links_pending_request(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'guest-code.db'}"
    settings = _settings(monkeypatch, database_url)
    reset_database_caches()
    initialize_database(settings)

    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        request = AuthService(db).request_guest_access(email="Guest.User@Example.COM")

    result = issue_guest_access_code(
        settings=settings,
        email="guest.user@example.com",
        ttl_seconds=120,
    )

    assert result.email == "guest.user@example.com"
    assert result.request_id == request.id
    assert result.code

    with session_factory() as db:
        request_row = db.get(GuestAccessRequestModel, request.id)
        code_row = db.scalar(select(GuestAccessCodeModel))

    assert request_row is not None
    assert request_row.status == "issued"
    assert code_row is not None
    assert code_row.request_id == request.id
    assert code_row.code_hash != result.code

    reset_database_caches()


def test_resolve_env_file_uses_named_pgvector_profiles() -> None:
    assert resolve_env_file(profile="pgvector.local") == Path(".env.pgvector.local")
    assert resolve_env_file(profile="pgvector.production") == Path(".env.pgvector.production")


def test_resolve_env_file_allows_explicit_override(tmp_path) -> None:
    env_file = tmp_path / "operator.env"

    assert resolve_env_file(profile="pgvector.local", env_file=env_file) == env_file


def test_send_guest_access_code_email_uses_configured_sender(monkeypatch) -> None:  # noqa: ANN001
    calls: list[dict[str, object]] = []

    class GuestCodeEmailSpy:
        def send_guest_access_code(
            self,
            *,
            recipient_email: str,
            code: str,
            expires_at: datetime,
            language: str = "ko",
        ) -> None:
            calls.append(
                {
                    "recipient_email": recipient_email,
                    "code": code,
                    "expires_at": expires_at,
                    "language": language,
                }
            )

    settings = Settings(_env_file=None, MY_AGENTS_RESPONSE_MODE="deterministic")
    result = GuestCodeIssueResult(
        email="guest@example.com",
        request_id="guest-request-id",
        code="guest-code-123",
        expires_at=datetime(2026, 6, 6, 12, 30, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "scripts.issue_guest_access_code.build_auth_email_sender",
        lambda active_settings: GuestCodeEmailSpy(),
    )

    send_guest_access_code_email(settings=settings, result=result, language="en")

    assert calls == [
        {
            "recipient_email": "guest@example.com",
            "code": "guest-code-123",
            "expires_at": datetime(2026, 6, 6, 12, 30, tzinfo=UTC),
            "language": "en",
        }
    ]


def test_main_prints_guest_code_before_email_failure(tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
    env_file = tmp_path / "operator.env"
    env_file.write_text("MY_AGENTS_RESPONSE_MODE=deterministic\n")
    issued = GuestCodeIssueResult(
        email="guest@example.com",
        request_id="guest-request-id",
        code="guest-code-123",
        expires_at=datetime(2026, 6, 6, 12, 30, tzinfo=UTC),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "issue_guest_access_code",
            "--env-file",
            str(env_file),
            "--email",
            "guest@example.com",
            "--send-email",
            "--lang",
            "en",
        ],
    )
    monkeypatch.setattr(
        "scripts.issue_guest_access_code.issue_guest_access_code", lambda **_: issued
    )

    def fail_email_send(**_: object) -> None:
        raise RuntimeError("email provider unavailable")

    monkeypatch.setattr(
        "scripts.issue_guest_access_code.send_guest_access_code_email", fail_email_send
    )

    exit_code = main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "code=guest-code-123" in captured.out
    assert "email_sent=False" in captured.out
    assert "email_language=en" in captured.out
    assert "failed to send guest access code email: RuntimeError" in captured.err
