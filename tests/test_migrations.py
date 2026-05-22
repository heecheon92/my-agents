"""Alembic migration smoke tests that stay offline by default."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from io import StringIO
from os import environ

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alembic import command
from my_agents.knowledge.models import DocumentChunkModel
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models


class _LegacyEmbeddingProvider:
    provider = "legacy-test"
    model = "legacy-test"
    dimensions = 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:  # noqa: ARG002
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:  # noqa: ARG002
        return [1.0, 0.0]


EXPECTED_SERVICE_TABLES = {
    "alembic_version",
    "users",
    "sessions",
    "auth_tokens",
    "guest_access_codes",
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


def _assert_pgvector_available_when_postgres(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        if engine.dialect.name != "postgresql":
            return
        with engine.connect() as connection:
            assert connection.exec_driver_sql("select to_regtype('vector')").scalar() is not None
            columns = {
                row[0]
                for row in connection.exec_driver_sql(
                    "select column_name from information_schema.columns "
                    "where table_name = 'document_chunks'"
                ).fetchall()
            }
            assert "embedding_vector" in columns
    finally:
        engine.dispose()


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
    assert "20260520_0003" in sql
    assert "20260520_0004" in sql
    assert "20260521_0005" in sql
    assert "20260521_0006" in sql
    assert "20260521_0007" in sql
    assert "20260522_0008" in sql
    assert "20260522_0009" in sql
    assert "embedding_vector" in sql
    assert "progress_percent" in sql
    assert "completed_at" in sql


def test_sqlite_json_fallback_reads_chunks_created_before_pgvector_migration(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "pre-pgvector.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", database_url)
    command.upgrade(_alembic_config(), "20260521_0006")

    engine = create_engine(database_url)
    document_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    now = datetime.now().isoformat(sep=" ")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "insert into documents "
                "(id, title, content, source_type, owner_user_id, created_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    "Legacy Chunk",
                    "Legacy vector fallback mentions LangGraph.",
                    "text",
                    "user-1",
                    now,
                ),
            )
            connection.exec_driver_sql(
                "insert into extraction_runs (id, document_id, status, created_at) "
                "values (?, ?, ?, ?)",
                (run_id, document_id, "completed", now),
            )
            connection.exec_driver_sql(
                "insert into document_chunks "
                "(id, document_id, extraction_run_id, ordinal, content, start_offset, "
                "end_offset, source_page, embedding_json) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    document_id,
                    run_id,
                    0,
                    "Legacy vector fallback mentions LangGraph.",
                    0,
                    39,
                    None,
                    json.dumps([1.0, 0.0]),
                ),
            )
        with Session(engine) as session:
            chunk = session.get(DocumentChunkModel, chunk_id)
            assert chunk is not None
            assert chunk.content == "Legacy vector fallback mentions LangGraph."

            results = RetrievalService(
                session,
                embedding_provider=_LegacyEmbeddingProvider(),
            ).retrieve(user_id="user-1", query="LangGraph")

        assert results
        assert results[0].chunk.id == chunk_id
    finally:
        engine.dispose()


def test_optional_external_test_database_runs_migrations(monkeypatch) -> None:
    test_database_url = environ.get("MY_AGENTS_TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("MY_AGENTS_TEST_DATABASE_URL is not set; skipping external DB smoke")

    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", test_database_url)

    command.upgrade(_alembic_config(), "head")

    _assert_database_matches_model_metadata(test_database_url)
    _assert_pgvector_available_when_postgres(test_database_url)
