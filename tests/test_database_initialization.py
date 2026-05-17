"""Database bootstrap policy tests for migration-aware persistence."""

from __future__ import annotations

from sqlalchemy import Engine

from my_agents.persistence.database import Base, initialize_database, reset_database_caches
from my_agents.persistence.models import import_all_models
from my_agents.settings import Settings


def test_import_all_models_registers_current_service_tables() -> None:
    import_all_models()

    assert {
        "users",
        "sessions",
        "groups",
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
