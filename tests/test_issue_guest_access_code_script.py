"""Manual guest access code issue script tests."""

from __future__ import annotations

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
from scripts.issue_guest_access_code import issue_guest_access_code, resolve_env_file


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
