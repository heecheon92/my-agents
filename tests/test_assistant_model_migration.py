"""Model preference migration preserves legacy rows without guessing their model."""

from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


@pytest.fixture(autouse=True)
def preserve_application_logging(monkeypatch):
    # Alembic fileConfig disables existing loggers globally; this test owns only schema changes.
    monkeypatch.setattr("logging.config.fileConfig", lambda *args, **kwargs: None)


def test_model_preferences_upgrade_and_downgrade_preserve_legacy_rows(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path}/models.db"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260905_0034")
    engine = create_engine(url)
    with engine.begin() as db:
        # Seed the schema at 0034, without current ORM defaults for columns introduced later.
        db.execute(
            text(
                "INSERT INTO users (id,email,nickname,password_hash,approval_status,account_type,"
                "user_type,created_at) VALUES ('user','test@example.com','Test','offline',"
                "'approved','registered','normal',CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO conversations (id,owner_user_id,title,created_at) "
                "VALUES ('conversation','user','fixture',CURRENT_TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO agent_runs (id,conversation_id,user_id,status,reasoning_mode,"
                "reasoning_effort,resolved_knowledge_base_count,created_at) VALUES "
                "('run','conversation','user','completed','standard','medium',0,CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "head")
    with engine.begin() as db:
        assert db.scalar(text("SELECT assistant_model_preference FROM users")) is None
        assert db.scalar(text("SELECT assistant_model FROM agent_runs")) is None
        db.execute(text("UPDATE users SET assistant_model_preference='gpt-6-astra'"))
        db.execute(text("UPDATE agent_runs SET assistant_model='gpt-6.1-sol'"))
    command.downgrade(config, "20260905_0034")
    assert "assistant_model_preference" not in {
        col["name"] for col in inspect(engine).get_columns("users")
    }
    assert "assistant_model" not in {
        col["name"] for col in inspect(engine).get_columns("agent_runs")
    }
    with engine.connect() as db:
        assert db.scalar(text("SELECT id FROM users")) == "user"
        assert db.scalar(text("SELECT id FROM agent_runs")) == "run"
    engine.dispose()


def test_model_preferences_postgres_sql_contains_nullable_columns(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "postgresql+psycopg://test:test@localhost/offline")
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "20260905_0034:20260930_0035", sql=True)
    sql = output.getvalue()
    assert "ALTER TABLE users ADD COLUMN assistant_model_preference VARCHAR(80)" in sql
    assert "ALTER TABLE agent_runs ADD COLUMN assistant_model VARCHAR(80)" in sql
    assert "NOT NULL" not in sql and "UPDATE users SET assistant_model_preference" not in sql
