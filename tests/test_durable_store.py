"""Real database tests for worker exclusion and atomic recoverable output."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.exc import IntegrityError

from my_agents.conversations.durable_models import (
    DurableRunFrame,
    DurableRunJob,
    DurableRunOutput,
    DurableWorkerHeartbeat,
)
from my_agents.conversations.durable_store import (
    append_frame,
    claim_job,
    enqueue_job,
    find_job,
    get_output,
    latest_terminal,
    list_frames,
    record_worker_heartbeat,
    renew_lease,
    request_fingerprint,
)
from my_agents.conversations.models import AgentRunModel, ConversationModel
from my_agents.persistence.database import Base, _engine_for_url, _sessionmaker_for_url


@pytest.fixture
def database(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'durable.sqlite'}"
    engine = _engine_for_url(url)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            ConversationModel.__table__,
            AgentRunModel.__table__,
            DurableRunJob.__table__,
            DurableRunOutput.__table__,
            DurableRunFrame.__table__,
            DurableWorkerHeartbeat.__table__,
        ],
    )
    yield url, _sessionmaker_for_url(url)
    engine.dispose()


def seed_job(sessions, key="key", kind="answer"):
    with sessions() as db:
        conversation = ConversationModel(owner_user_id="owner")
        db.add(conversation)
        db.flush()
        run = AgentRunModel(conversation_id=conversation.id, user_id="owner", status="running")
        db.add(run)
        db.flush()
        job = enqueue_job(
            db,
            run_id=run.id,
            user_id="owner",
            kind=kind,
            client_request_id=key,
            request_json={"conversation_id": conversation.id, "message": "hello"},
        )
        db.commit()
        return run.id, job.id


def test_concurrent_claims_execute_a_job_at_most_once(database):
    url, sessions = database
    expected = {seed_job(sessions, str(i))[1] for i in range(6)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim_job(url, 60), range(12)))
    claims = [item for item in results if item is not None]
    assert {item[0] for item in claims} == expected
    assert len(claims) == len(expected)
    assert len({item[1] for item in claims}) == len(expected)
    assert claim_job(url, 60) is None


def test_renewal_cannot_steal_or_resurrect_ownership(database):
    url, sessions = database
    _, job_id = seed_job(sessions)
    claimed_id, token = claim_job(url, 60)
    assert claimed_id == job_id
    assert not renew_lease(url, job_id, str(uuid.uuid4()), 60)
    assert renew_lease(url, job_id, token, 60)
    with sessions() as db:
        db.execute(
            update(DurableRunJob)
            .where(DurableRunJob.id == job_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    assert not renew_lease(url, job_id, token, 60)
    assert claim_job(url, 60) is None  # lost work is failed elsewhere, never regenerated
    record_worker_heartbeat(url, "worker-1")
    record_worker_heartbeat(url, "worker-1")
    with sessions() as db:
        assert len(db.scalars(select(DurableWorkerHeartbeat)).all()) == 1


def test_snapshot_and_remainder_are_atomic_and_ordered(database):
    _, sessions = database
    run_id, _ = seed_job(sessions)
    with sessions() as db:
        append_frame(db, run_id, "answer_delta", {"delta": "one", "sequence": 1})
        db.commit()
    with sessions() as writer, sessions() as reader:
        append_frame(writer, run_id, "answer_delta", {"delta": "two", "sequence": 2})
        output = get_output(reader, run_id)
        assert output.cursor == 1
        assert json.loads(output.snapshot_json)["partial_reply"] == "one"
        assert [f.cursor for f in list_frames(reader, run_id, 0)] == [1]
        snapshot_cursor = output.cursor
        reader.rollback()
        writer.commit()
        remainder = list_frames(reader, run_id, snapshot_cursor)
        assert [f.cursor for f in remainder] == [2]
        assert "one" + json.loads(remainder[0].data_json)["delta"] == "onetwo"
    with sessions() as db:
        append_frame(db, run_id, "answer_delta", {"delta": "rollback", "sequence": 3})
        db.rollback()
        assert get_output(db, run_id).cursor == 2
        assert json.loads(get_output(db, run_id).snapshot_json)["partial_reply"] == "onetwo"


def test_concurrent_append_serializes_projection(database):
    _, sessions = database
    run_id, _ = seed_job(sessions)

    def write(index):
        with sessions() as db:
            frame = append_frame(
                db, run_id, "answer_delta", {"delta": f"{index},", "sequence": index + 1}
            )
            db.commit()
            return frame.cursor

    with ThreadPoolExecutor(max_workers=8) as pool:
        cursors = list(pool.map(write, range(20)))
    assert sorted(cursors) == list(range(1, 21))
    with sessions() as db:
        frames = list_frames(db, run_id, 0)
        expected = "".join(json.loads(frame.data_json)["delta"] for frame in frames)
        output = get_output(db, run_id)
        assert output.cursor == 20
        assert json.loads(output.snapshot_json)["partial_reply"] == expected


def test_resume_keeps_answer_cursor_and_replaces_waiting_control(database):
    _, sessions = database
    run_id, _ = seed_job(sessions)
    interaction = {"type": "future_input", "version": 42}
    result = {"reply": "canonical answer", "reasoning_summaries": []}
    with sessions() as db:
        append_frame(db, run_id, "answer_delta", {"delta": "before", "sequence": 1})
        append_frame(db, run_id, "run_interrupted", {"interaction": interaction})
        assert latest_terminal(db, run_id).event == "run_interrupted"
        append_frame(db, run_id, "run_started", {"run_id": run_id, "conversation_id": "cid"})
        assert latest_terminal(db, run_id).event == "run_interrupted"
        append_frame(
            db,
            run_id,
            "run_resumed",
            {
                "run_id": run_id,
                "interaction_id": "interaction",
                "interaction_schema_version": 2,
                "interaction_type": "document_selection",
            },
        )
        assert latest_terminal(db, run_id) is None
        append_frame(db, run_id, "answer_delta", {"delta": "after", "sequence": 1})
        append_frame(db, run_id, "run_completed", result)
        db.commit()
        output = get_output(db, run_id)
        snapshot = json.loads(output.snapshot_json)
        assert output.cursor == 6
        assert snapshot["partial_reply"] == "beforeafter"
        assert snapshot["interaction"] is None
        assert snapshot["result"] == result
        assert json.loads(latest_terminal(db, run_id).data_json) == result


def test_pruning_preserves_projection_and_terminal_marker(database):
    _, sessions = database
    run_id, _ = seed_job(sessions)
    with sessions() as db:
        append_frame(db, run_id, "run_failed", {"code": "worker_lost", "detail": "Retry."})
        for index in range(8):
            append_frame(
                db,
                run_id,
                "user_message_stored",
                {"message_id": str(index), "content_length": 0},
                max_frames=3,
            )
        db.commit()
        output = get_output(db, run_id)
        assert output.cursor == 9
        assert output.min_cursor == 6
        assert [f.cursor for f in list_frames(db, run_id, output.min_cursor)] == [7, 8, 9]
        snapshot = json.loads(output.snapshot_json)
        assert snapshot["error"] == {"code": "worker_lost", "detail": "Retry."}
        assert [item["id"] for item in snapshot["activity_events"]] == [7, 8, 9]
        terminal = latest_terminal(db, run_id)
        assert terminal.cursor == 1
        assert terminal.event == "run_failed"


def test_operation_key_uniqueness_and_transaction_rollback(database):
    _, sessions = database
    run_id, _ = seed_job(sessions)
    with sessions() as db:
        original = find_job(db, "owner", "answer", "key")
        assert original.run_id == run_id
        with pytest.raises(IntegrityError):
            enqueue_job(
                db,
                run_id=run_id,
                user_id="owner",
                kind="answer",
                client_request_id="key",
                request_json={"different": True},
            )
        db.rollback()
        enqueue_job(
            db,
            run_id=run_id,
            user_id="owner",
            kind="resume",
            client_request_id="key",
            request_json={"answer": "yes"},
        )
        db.rollback()
        assert find_job(db, "owner", "resume", "key") is None
        assert len(db.scalars(select(DurableRunJob)).all()) == 1
    assert request_fingerprint({"b": 1, "a": 2}) == request_fingerprint({"a": 2, "b": 1})
    assert request_fingerprint({"a": 1}) != request_fingerprint({"a": 2})


def test_run_erasure_cascades_all_durable_content(database):
    _, sessions = database
    run_id, _ = seed_job(sessions)
    with sessions() as db:
        append_frame(db, run_id, "answer_delta", {"delta": "private", "sequence": 1})
        db.commit()
        db.execute(delete(AgentRunModel).where(AgentRunModel.id == run_id))
        db.commit()
        assert db.scalar(select(DurableRunJob)) is None
        assert db.scalar(select(DurableRunOutput)) is None
        assert db.scalar(select(DurableRunFrame)) is None


def test_activity_metadata_is_allowlisted_and_terminals_are_not_duplicated(database):
    _, sessions = database
    run_id, _ = seed_job(sessions)
    with sessions() as db:
        frame = append_frame(
            db,
            run_id,
            "user_message_stored",
            {
                "message_id": "message",
                "content_length": 12,
                "raw_prompt": "private",
                "provider_credentials": "not-a-real-key",
            },
        )
        expected = {"message_id": "message", "content_length": 12}
        assert json.loads(frame.data_json) == expected
        append_frame(
            db,
            run_id,
            "run_cancelled",
            {
                "run_id": run_id,
                "conversation_id": "cid",
                "status": "cancelled",
                "partial_reply_persisted": False,
            },
        )
        projection = json.loads(get_output(db, run_id).snapshot_json)
        assert projection["activity_events"] == [
            {"id": 1, "event": "user_message_stored", "data": expected}
        ]
        with pytest.raises(ValueError):
            append_frame(db, run_id, "internal_debug", {"secret": "not-real"})
        assert get_output(db, run_id).cursor == 2


def test_reasoning_projection_uses_canonical_summary_schema(database):
    from my_agents.conversations.schemas import ReasoningSummaryItem

    _, sessions = database
    run_id, _ = seed_job(sessions)
    with sessions() as db:
        for i in range(2):
            append_frame(
                db,
                run_id,
                "reasoning_summary_delta",
                {
                    "stage": "answer_synthesis",
                    "delta": "x" * 300,
                    "sequence": i + 1,
                },
            )
        projection = json.loads(get_output(db, run_id).snapshot_json)
        summary = ReasoningSummaryItem.model_validate(projection["reasoning_summaries"][0])
        assert summary.source == "provider_reasoning_summary"
        assert len(summary.text) == 500
        assert projection["activity_events"] == []
        canonical = {
            "stage": "answer_synthesis",
            "text": "Concise final explanation.",
            "source": "model_generated",
        }
        append_frame(db, run_id, "reasoning_summary_generated", canonical)
        projection = json.loads(get_output(db, run_id).snapshot_json)
        assert projection["reasoning_summaries"] == [canonical]


def test_worker_fence_rolls_back_output_after_ownership_loss(database):
    from my_agents.conversations.durable_runtime import ExecutionStopped, install_fence

    url, sessions = database
    run_id, job_id = seed_job(sessions)
    _, token = claim_job(url, 60)
    with sessions() as worker, sessions() as replacement:
        install_fence(worker, job_id, token)
        replacement.execute(
            update(DurableRunJob)
            .where(DurableRunJob.id == job_id)
            .values(lease_token="replacement-owner")
        )
        replacement.commit()
        with pytest.raises(ExecutionStopped):
            append_frame(worker, run_id, "answer_delta", {"delta": "lost", "sequence": 1})
            worker.commit()
        worker.rollback()
        output = get_output(replacement, run_id)
        assert output.cursor == 0
        assert json.loads(output.snapshot_json)["partial_reply"] == ""


def test_terminal_flush_cannot_overwrite_an_explicit_stop(database):
    from my_agents.conversations.durable_runtime import ExecutionStopped, emit, install_fence

    url, sessions = database
    run_id, job_id = seed_job(sessions)
    _, token = claim_job(url, 60)
    with sessions() as worker, sessions() as cancelling:
        run = worker.get(AgentRunModel, run_id)
        install_fence(worker, job_id, token)
        cancelling.execute(
            update(AgentRunModel).where(AgentRunModel.id == run_id).values(status="cancelling")
        )
        cancelling.commit()
        run.status = "completed"
        with pytest.raises(ExecutionStopped):
            emit(worker, run, "run_completed", {"reply": "too late"})
            worker.commit()
        worker.rollback()
        assert cancelling.get(AgentRunModel, run_id, populate_existing=True).status == "cancelling"
        assert get_output(cancelling, run_id).terminal_event is None


def test_partial_reply_limit_applies_across_resume_and_rejects_without_mutation(database):
    from my_agents.conversations.durable_store import MAX_PARTIAL_REPLY_CHARACTERS

    _, sessions = database
    run_id, _ = seed_job(sessions)
    with sessions() as db:
        append_frame(
            db, run_id, "answer_delta", {"delta": "x" * MAX_PARTIAL_REPLY_CHARACTERS, "sequence": 1}
        )
        append_frame(db, run_id, "run_interrupted", {"interaction": {"type": "future"}})
        append_frame(
            db,
            run_id,
            "run_resumed",
            {
                "run_id": run_id,
                "interaction_id": "interaction",
                "interaction_schema_version": 2,
                "interaction_type": "document_selection",
            },
        )
        db.commit()
        before = get_output(db, run_id)
        before_json, before_cursor = before.snapshot_json, before.cursor
        with pytest.raises(ValueError, match="durable output limit"):
            append_frame(db, run_id, "answer_delta", {"delta": "!", "sequence": 1})
        db.commit()  # Even a caught validation error cannot advance the cursor.
        after = get_output(db, run_id)
        assert after.cursor == before_cursor
        assert after.snapshot_json == before_json
        assert list_frames(db, run_id, before_cursor) == []
