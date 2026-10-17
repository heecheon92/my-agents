"""Migration behavior on SQLite and PostgreSQL SQL generation."""

import logging.config
from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command


@pytest.fixture(autouse=True)
def preserve_test_logging(monkeypatch):
    # Alembic's application logging setup must not disable pytest capture or
    # loggers belonging to later tests in the same process.
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite'}"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20261003_0039")
    command.upgrade(config, "20261017_0040")
    engine = create_engine(url)
    yield config, engine
    engine.dispose()


def test_durable_migration_schema_and_roundtrip(migrated):
    config, engine = migrated
    schema = inspect(engine)
    for table in ("durable_run_jobs", "durable_run_outputs", "durable_run_frames"):
        assert schema.get_foreign_keys(table)[0]["options"]["ondelete"] == "CASCADE"
    unique = schema.get_unique_constraints("durable_run_jobs")
    assert any(item["column_names"] == ["user_id", "kind", "client_request_id"] for item in unique)
    with engine.begin() as db:
        db.execute(text("INSERT INTO conversations VALUES ('c', 'u', 'test', CURRENT_TIMESTAMP)"))
        db.execute(
            text(
                "INSERT INTO agent_runs (id,conversation_id,user_id,status,reasoning_mode,"
                "reasoning_effort,resolved_knowledge_base_count,created_at) "
                "VALUES ('r','c','u','queued','standard','medium',0,CURRENT_TIMESTAMP)"
            )
        )
    with engine.begin() as db, pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO agent_runs (id,conversation_id,user_id,status,reasoning_mode,"
                "reasoning_effort,resolved_knowledge_base_count,created_at) "
                "VALUES ('r2','c','u','running','standard','medium',0,CURRENT_TIMESTAMP)"
            )
        )
    with pytest.raises(RuntimeError, match="Drain durable jobs"):
        command.downgrade(config, "20261003_0039")
    assert "durable_run_outputs" in inspect(engine).get_table_names()
    with engine.begin() as db:
        db.execute(text("UPDATE agent_runs SET status='waiting_for_input' WHERE id='r'"))
        db.execute(
            text(
                "INSERT INTO durable_run_outputs (run_id,cursor,min_cursor,snapshot_json) "
                "VALUES ('r',0,0,'{}')"
            )
        )
    with pytest.raises(RuntimeError, match="Drain durable jobs"):
        command.downgrade(config, "20261003_0039")
    with engine.begin() as db:
        db.execute(text("UPDATE agent_runs SET status='cancelled' WHERE id='r'"))
    command.downgrade(config, "20261003_0039")
    assert "durable_run_outputs" not in inspect(engine).get_table_names()
    index = next(
        item
        for item in inspect(engine).get_indexes("agent_runs")
        if item["name"] == "uq_agent_runs_active_conversation"
    )
    assert "queued" not in str(index["dialect_options"]["sqlite_where"])
    command.upgrade(config, "20261017_0040")
    assert "durable_run_outputs" in inspect(engine).get_table_names()


def test_postgresql_durable_migration_offline_sql(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "postgresql+psycopg://localhost/offline_test")
    buffer = StringIO()
    config = Config("alembic.ini", output_buffer=buffer)
    command.upgrade(config, "20261003_0039:20261017_0040", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE durable_run_jobs" in sql
    assert "ON DELETE CASCADE" in sql
    assert "UNIQUE (user_id, kind, client_request_id)" in sql
    assert "WHERE status IN ('queued', 'running', 'waiting_for_input', 'cancelling')" in sql
    buffer.seek(0)
    buffer.truncate()
    command.downgrade(config, "20261017_0040:20261003_0039", sql=True)
    sql = buffer.getvalue()
    assert "RAISE EXCEPTION 'Drain durable jobs before downgrade'" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP TABLE durable_run_jobs")
