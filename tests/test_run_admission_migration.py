"""Migration refuses ambiguous live state and never chooses a winning run."""

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from my_agents.conversations.models import AgentRunModel, ConversationModel


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
    with Session(engine) as db:
        db.add(ConversationModel(id="one", owner_user_id="user"))
        db.flush()
        db.add_all(
            [
                AgentRunModel(id=str(i), conversation_id="one", user_id="user", status="running")
                for i in range(2 if duplicates else 1)
            ]
        )
        db.commit()
    if duplicates:
        with pytest.raises(RuntimeError, match="duplicate active conversations"):
            command.upgrade(config, "head")
        with Session(engine) as db:
            assert list(db.scalars(select(AgentRunModel.status))) == ["running", "running"]
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
        with Session(engine) as db:
            assert list(db.scalars(select(AgentRunModel.status))) == ["running"]
    engine.dispose()
