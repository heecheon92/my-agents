"""Database bootstrap policy tests for migration-aware persistence."""

from __future__ import annotations

from sqlalchemy import Engine

from my_agents.persistence.database import (
    Base,
    _engine_kwargs_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.persistence.models import import_all_models
from my_agents.settings import Settings


def test_import_all_models_registers_current_service_tables() -> None:
    import_all_models()

    assert {
        "users",
        "sessions",
        "groups",
        "group_invitations",
        "memberships",
        "conversations",
        "messages",
        "agent_runs",
        "agent_events",
        "knowledge_bases",
        "documents",
        "document_permissions",
        "extraction_runs",
        "document_chunks",
        "entities",
        "entity_mentions",
        "entity_relationships",
        "citations",
        "user_memory_settings",
        "user_memories",
        "memory_suggestions",
    }.issubset(Base.metadata.tables)


def test_initialize_database_auto_creates_default_in_memory_sqlite(
    monkeypatch,
) -> None:
    reset_database_caches()
    calls: list[Engine] = []

    def record_create_all(engine: Engine) -> None:
        calls.append(engine)

    monkeypatch.setattr(Base.metadata, "create_all", record_create_all)
    settings = Settings(_env_file=None, MY_AGENTS_RESPONSE_MODE="deterministic")

    initialize_database(settings)

    assert len(calls) == 1


def test_initialize_database_skips_postgres_auto_create_by_default(
    monkeypatch,
) -> None:
    reset_database_caches()
    calls: list[Engine] = []

    def record_create_all(engine: Engine) -> None:
        calls.append(engine)

    monkeypatch.setattr(Base.metadata, "create_all", record_create_all)
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_DATABASE_URL="postgresql+psycopg://app:pw@db/app",
    )

    initialize_database(settings)

    assert calls == []


def test_postgres_engine_pre_pings_and_recycles_idle_connections() -> None:
    kwargs = _engine_kwargs_for_url("postgresql+psycopg://app:pw@db/app")

    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 300


def test_in_memory_sqlite_engine_keeps_static_pool() -> None:
    kwargs = _engine_kwargs_for_url("sqlite+pysqlite:///:memory:")

    assert kwargs["connect_args"] == {"check_same_thread": False}
    assert kwargs["poolclass"].__name__ == "StaticPool"
