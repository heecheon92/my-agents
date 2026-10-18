"""Migration refuses ambiguous live state and never chooses a winning run."""

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite+pysqlite:///{tmp_path}/upgrade.db"
        return
    url = os.environ.get("MY_AGENTS_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL test server with CREATEDB privilege not configured")
    # Never migrate the supplied database. Create/drop only this randomly named test DB.
    name = "admission_migration_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(url).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.parametrize("duplicates", [False, True])
def test_active_run_index_upgrade_and_downgrade(migration_url, monkeypatch, duplicates):
    url = migration_url
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260825_0033")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO conversations (id,owner_user_id,title,created_at) "
                "VALUES ('one','user','fixture',CURRENT_TIMESTAMP)"
            )
        )
        # Current ORM defaults include later columns; seed only the 0033 schema.
        for i in range(2 if duplicates else 1):
            db.execute(
                text(
                    "INSERT INTO agent_runs (id,conversation_id,user_id,status,reasoning_mode,"
                    "reasoning_effort,resolved_knowledge_base_count,created_at) VALUES "
                    "(:id,'one','user','running','standard','medium',0,CURRENT_TIMESTAMP)"
                ),
                {"id": str(i)},
            )
    if duplicates:
        with pytest.raises(RuntimeError, match="duplicate active conversations"):
            command.upgrade(config, "head")
        with engine.connect() as db:
            assert list(db.scalars(text("SELECT status FROM agent_runs"))) == ["running", "running"]
        assert "uq_agent_runs_active_conversation" not in {
            i["name"] for i in inspect(engine).get_indexes("agent_runs")
        }
    else:
        command.upgrade(config, "head")
        assert "uq_agent_runs_active_conversation" in {
            i["name"] for i in inspect(engine).get_indexes("agent_runs")
        }
        command.downgrade(config, "20260825_0033")
        assert "uq_agent_runs_active_conversation" not in {
            i["name"] for i in inspect(engine).get_indexes("agent_runs")
        }
        with engine.connect() as db:
            assert list(db.scalars(text("SELECT status FROM agent_runs"))) == ["running"]
    engine.dispose()
