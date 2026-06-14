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
    "guest_access_requests",
    "guest_access_codes",
    "groups",
    "group_invitations",
    "memberships",
    "conversations",
    "messages",
    "agent_runs",
    "agent_events",
    "knowledge_bases",
    "knowledge_base_publications",
    "knowledge_publish_requests",
    "documents",
    "document_permissions",
    "document_parse_artifacts",
    "extraction_runs",
    "document_chunks",
    "document_metadata_profiles",
    "entities",
    "entity_mentions",
    "entity_relationships",
    "structured_knowledge_entities",
    "citations",
    "user_memory_settings",
    "user_memories",
    "memory_suggestions",
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
    assert "20260524_0010" in sql
    assert "20260524_0011" in sql
    assert "20260524_0012" in sql
    assert "20260524_0013" in sql
    assert "20260524_0014" in sql
    assert "20260525_0015" in sql
    assert "20260526_0016" in sql
    assert "20260607_0017" in sql
    assert "20260607_0018" in sql
    assert "20260607_0019" in sql
    assert "20260607_0020" in sql
    assert "20260609_0021" in sql
    assert "20260609_0022" in sql
    assert "20260609_0023" in sql
    assert "20260609_0024" in sql
    assert "20260610_0025" in sql
    assert "20260614_0026" in sql
    assert "20260614_0027" in sql
    assert "CREATE TABLE guest_access_requests" in sql
    assert "CREATE TABLE group_invitations" in sql
    assert "CREATE TABLE knowledge_publish_requests" in sql
    assert "CREATE TABLE knowledge_base_publications" in sql
    assert "CREATE TABLE document_parse_artifacts" in sql
    assert "CREATE TABLE user_memory_settings" in sql
    assert "CREATE TABLE user_memories" in sql
    assert "CREATE TABLE memory_suggestions" in sql
    assert "CREATE TABLE structured_knowledge_entities" in sql
    assert "CREATE TABLE document_metadata_profiles" in sql
    assert "source_location_json" in sql
    assert "embedding_vector" in sql
    assert "progress_percent" in sql
    assert "completed_at" in sql
    assert "purpose" in sql
    assert "DROP COLUMN group_id" in sql
    assert "approval_status" in sql
    assert "memory_source_snapshot_json" in sql
    assert "uq_group_invitations_pending_email" in sql
    assert "token_hash" in sql
    assert "nickname" in sql
    assert "user_type" in sql


def test_parse_artifacts_store_only_derived_parser_outputs(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "parse-artifacts.db"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")

    command.upgrade(_alembic_config(), "head")

    with sqlite3.connect(database_path) as connection:
        artifact_columns = {
            row[1]
            for row in connection.execute("pragma table_info(document_parse_artifacts)").fetchall()
        }
        chunk_columns = {
            row[1] for row in connection.execute("pragma table_info(document_chunks)").fetchall()
        }
        entity_columns = {
            row[1]
            for row in connection.execute(
                "pragma table_info(structured_knowledge_entities)"
            ).fetchall()
        }

    assert {
        "document_id",
        "source_sha256",
        "source_filename",
        "source_content_type",
        "source_type",
        "parser_provider",
        "parser_name",
        "markdown_content",
        "elements_json",
        "warnings_json",
    }.issubset(artifact_columns)
    assert "source_location_json" in chunk_columns
    assert "source_location_json" in entity_columns

    forbidden_fragments = ("blob", "bytes", "object_key", "storage_provider")
    assert not any(
        fragment in column for column in artifact_columns for fragment in forbidden_fragments
    )


def test_legacy_documents_without_knowledge_base_upgrade_to_head(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "legacy-documents.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", database_url)

    command.upgrade(_alembic_config(), "20260520_0004")
    document_id = str(uuid.uuid4())
    owner_user_id = str(uuid.uuid4())
    now = datetime.now().isoformat(sep=" ")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into users (id, email, password_hash, created_at) values (?, ?, ?, ?)",
            (owner_user_id, "legacy-doc@example.com", "hash", now),
        )
        connection.execute(
            "insert into documents "
            "(id, title, content, owner_user_id, group_id, knowledge_base_id, created_at) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (document_id, "Legacy doc", "legacy content", owner_user_id, None, None, now),
        )

    command.upgrade(_alembic_config(), "head")

    with sqlite3.connect(database_path) as connection:
        document_kb_id = connection.execute(
            "select knowledge_base_id from documents where id = ?", (document_id,)
        ).fetchone()[0]
        migrated_kb = connection.execute(
            "select scope, owner_user_id from knowledge_bases where id = ?", (document_kb_id,)
        ).fetchone()
        user_columns = {row[1] for row in connection.execute("pragma table_info(users)").fetchall()}
        legacy_nickname = connection.execute(
            "select nickname from users where id = ?", (owner_user_id,)
        ).fetchone()[0]
        legacy_user_type = connection.execute(
            "select user_type from users where id = ?", (owner_user_id,)
        ).fetchone()[0]
        alembic_version = connection.execute("select version_num from alembic_version").fetchone()[
            0
        ]

    assert document_kb_id is not None
    assert migrated_kb == ("personal", owner_user_id)
    assert "approval_status" in user_columns
    assert "nickname" in user_columns
    assert "user_type" in user_columns
    assert legacy_nickname == "legacy-doc"
    assert legacy_user_type == "normal"
    assert alembic_version == "20260614_0027"

    _assert_database_matches_model_metadata(database_url)


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
            # This regression test intentionally stops at the pre-pgvector migration
            # boundary, but it still exercises the current ORM/retrieval models.
            # Add newer nullable provenance columns as a local compatibility shim so
            # the test remains focused on legacy embedding_json fallback behavior.
            connection.exec_driver_sql(
                "alter table document_chunks add column source_location_json text"
            )
            knowledge_base_id = str(uuid.uuid4())
            connection.exec_driver_sql(
                "insert into knowledge_bases "
                "(id, name, scope, owner_user_id, created_at) values (?, ?, ?, ?, ?)",
                (knowledge_base_id, "Legacy KB", "personal", "user-1", now),
            )
            connection.exec_driver_sql(
                "insert into documents "
                "(id, title, content, source_type, owner_user_id, knowledge_base_id, created_at) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    "Legacy Chunk",
                    "Legacy vector fallback mentions LangGraph.",
                    "text",
                    "user-1",
                    knowledge_base_id,
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
