"""Independent-connection races and transaction rollback at run admission."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from my_agents.api.conversations.run_lifecycle import admit_run, assert_no_active_run
from my_agents.api.errors import APIHTTPException
from my_agents.conversations.models import (
    AgentEventModel,
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models


@pytest.fixture(params=["sqlite", "postgresql"])
def admission_engine(request, tmp_path):
    if request.param == "postgresql":
        url = os.environ.get("MY_AGENTS_TEST_DATABASE_URL", "")
        if not url.startswith("postgresql"):
            pytest.skip("dedicated PostgreSQL test database not configured")
        # A unique schema isolates this test from every other test/application table.
        import uuid

        schema = "admission_" + uuid.uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            url, connect_args={"options": f"-csearch_path={schema},public"}
        ).execution_options(schema_translate_map={None: schema})
    else:
        engine = create_engine(f"sqlite:///{tmp_path}/admission.db")
    try:
        import_all_models()
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all(
                [ConversationModel(id=name, owner_user_id="user") for name in ("one", "two")]
            )
            db.add(
                MessageModel(id="prompt", conversation_id="one", role="user", content="Original")
            )
            db.commit()
        yield engine
    finally:
        engine.dispose()
        if request.param == "postgresql":
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def admit(db, conversation="one", replay=False):
    return admit_run(
        db=db,
        conversation_id=conversation,
        user_id="user",
        selection_context=KnowledgeBaseSelectionContext(
            mode="all", knowledge_base_ids=(), resolved_count=0
        ),
        reasoning_mode="standard",
        reasoning_effort="low",
        **(
            {"existing_user_message": db.get(MessageModel, "prompt")}
            if replay
            else {"message": "New"}
        ),
    )


@pytest.mark.parametrize("replay", [False, True])
def test_two_stale_admission_checks_admit_only_one_run(admission_engine, replay):
    barrier = Barrier(2)

    def compete(is_replay):
        with Session(admission_engine) as db:
            assert_no_active_run(db, "one")
            barrier.wait(timeout=5)
            try:
                result = admit(db, replay=is_replay)
                return (200, result.run.id, is_replay)
            except APIHTTPException as exc:
                assert exc.code == "conversation_run_already_active"
                return (exc.status_code, None, is_replay)

    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(compete, False)
        b = workers.submit(compete, replay)
        outcomes = [a.result(timeout=10), b.result(timeout=10)]
    assert sorted(status for status, _, _ in outcomes) == [200, 409]
    winner = next(item for item in outcomes if item[0] == 200)
    with Session(admission_engine) as db:
        assert db.scalar(select(func.count()).select_from(AgentRunModel)) == 1
        assert db.scalar(select(func.count()).select_from(AgentEventModel)) == 2
        assert db.scalar(select(func.count()).select_from(MessageModel)) == (1 if winner[2] else 2)


def test_other_conversation_and_terminal_run_allow_new_admission(admission_engine):
    with Session(admission_engine) as db:
        first = admit(db).run
        admit(db, "two")
        for status in ("completed", "failed", "cancelled"):
            first.status = status
            db.commit()
            first = admit(db).run


@pytest.mark.parametrize("state", ["running", "waiting_for_input", "cancelling"])
def test_all_active_states_reject_competitors(admission_engine, state):
    with Session(admission_engine) as db:
        first = admit(db).run
        first.status = state
        db.commit()
        with pytest.raises(APIHTTPException) as error:
            admit(db)
        assert error.value.status_code == 409
        assert db.scalar(select(func.count()).select_from(MessageModel)) == 2


def test_unrelated_integrity_error_rolls_back_without_409(admission_engine, monkeypatch):
    from my_agents.api.conversations import run_lifecycle

    def broken(*args, **kwargs):
        raise IntegrityError("test", {}, ValueError("unrelated constraint"))

    monkeypatch.setattr(run_lifecycle, "append_run_event", broken)
    with Session(admission_engine) as db:
        with pytest.raises(IntegrityError):
            admit(db)
        assert db.scalar(select(func.count()).select_from(AgentRunModel)) == 0
        assert db.scalar(select(func.count()).select_from(MessageModel)) == 1
