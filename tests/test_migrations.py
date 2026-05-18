"""Alembic migration smoke tests that stay offline by default."""

from __future__ import annotations

import sqlite3
from io import StringIO
from os import environ

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from alembic import command
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models

EXPECTED_SERVICE_TABLES = {
    "alembic_version",
    "users",
    "sessions",
    "auth_tokens",
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
}


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _assert_database_matches_model_metadata(database_url: str) -> None:
    import_all_models()
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == []


def test_alembic_upgrade_head_creates_current_service_schema(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration-smoke.db"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")

    config = _alembic_config()
    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }

    assert EXPECTED_SERVICE_TABLES.issubset(tables)

    _assert_database_matches_model_metadata(f"sqlite+pysqlite:///{database_path}")


def test_alembic_offline_sql_generation_covers_initial_schema(monkeypatch) -> None:
    monkeypatch.delenv("MY_AGENTS_DATABASE_URL", raising=False)
    output = StringIO()
    config = _alembic_config()
    config.output_buffer = output

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE auth_tokens" in sql
    assert "CREATE TABLE citations" in sql
    assert "20260517_0001" in sql
    assert "20260518_0002" in sql


def test_optional_external_test_database_runs_migrations(monkeypatch) -> None:
    test_database_url = environ.get("MY_AGENTS_TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("MY_AGENTS_TEST_DATABASE_URL is not set; skipping external DB smoke")

    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", test_database_url)

    command.upgrade(_alembic_config(), "head")

    _assert_database_matches_model_metadata(test_database_url)
