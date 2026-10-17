"""Durable HTTP admission, independent execution and reconnect integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url

from my_agents.answer_worker import process_once
from my_agents.api.conversations.endpoints.durable import subscribe_output
from my_agents.auth.contracts import Principal
from my_agents.auth.models import SessionModel, UserModel
from my_agents.conversations.durable_models import DurableRunFrame, DurableRunJob, DurableRunOutput
from my_agents.conversations.durable_runtime import reap_lost_jobs
from my_agents.conversations.durable_store import claim_job, record_worker_heartbeat
from my_agents.conversations.models import AgentRunModel
from my_agents.persistence.database import Base, _engine_for_url, _sessionmaker_for_url
from my_agents.persistence.models import import_all_models
from my_agents.settings import get_settings

from .test_conversations_api import StreamingSpyGraph, _client, _signup_login, _TextChunk


@pytest.fixture(params=["sqlite", "postgresql"])
def durable(request, monkeypatch, tmp_path):
    admin = None
    engine = None
    schema = None
    if request.param == "postgresql":
        test_url = os.environ.get("MY_AGENTS_TEST_DATABASE_URL", "")
        if not test_url.startswith("postgresql"):
            pytest.skip("Optional isolated PostgreSQL integration database not configured")
        schema = "durable_api_" + uuid.uuid4().hex
        admin = create_engine(test_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        def cleanup_schema():
            if engine is not None:
                engine.dispose()
            with admin.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()

        request.addfinalizer(cleanup_schema)
        url = (
            make_url(test_url)
            .update_query_dict({"options": f"-csearch_path={schema},public"})
            .render_as_string(hide_password=False)
        )
        engine = _engine_for_url(url)
        import_all_models()
        # Explicit schema qualification prevents create_all checkfirst from seeing
        # identically named public tables and silently sharing another test's data.
        with engine.begin() as connection:
            Base.metadata.create_all(
                connection.execution_options(schema_translate_map={None: schema})
            )
    else:
        url = f"sqlite+pysqlite:///{tmp_path / 'api.sqlite'}"
    monkeypatch.setenv("MY_AGENTS_DURABLE_RUNS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", url)
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true" if schema is None else "false")
    monkeypatch.setenv("MY_AGENTS_DURABLE_RUN_POLL_SECONDS", "0.05")
    graph = StreamingSpyGraph()
    client = _client(monkeypatch, graph)
    try:
        user_id = _signup_login(client, "durable-owner@example.com")
        settings = get_settings()
        record_worker_heartbeat(settings.database_url, "test-worker")
        response = client.post("/conversations", json={"title": "Durable test"})
        assert response.status_code == 201
        yield client, settings, graph, response.json()["id"], user_id
    finally:
        client.close()


def admit(client, cid, *, key=None, message="Hello"):
    body = {"message": message, "client_request_id": key or str(uuid.uuid4())}
    response = client.post(f"/conversations/{cid}/runs/async", json=body)
    return response, body


def events(response):
    assert response.status_code == 200, response.text
    result = []
    for block in response.text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        values = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        result.append((int(values["id"]), values["event"].strip(), json.loads(values["data"])))
    return result


def test_admission_is_queued_idempotent_and_worker_owned(durable):
    client, settings, graph, cid, _ = durable
    response, body = admit(client, cid)
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["status"] == "queued"
    assert not graph.calls
    same = client.post(f"/conversations/{cid}/runs/async", json=body)
    assert same.status_code == 202
    assert same.json() == receipt
    runs = client.get(f"/conversations/{cid}/runs").json()
    assert any(run["run_id"] == receipt["run_id"] and run["status"] == "queued" for run in runs)
    changed = client.post(f"/conversations/{cid}/runs/async", json={**body, "message": "Different"})
    assert changed.status_code == 409
    assert changed.json()["code"] == "run_request_id_conflict"
    distinct, _ = admit(client, cid)
    assert distinct.status_code == 409
    assert distinct.json()["code"] == "conversation_run_already_active"
    assert process_once(settings, graph=graph)
    assert len(graph.calls) == 1
    finished = client.post(f"/conversations/{cid}/runs/async", json=body)
    assert finished.status_code == 202
    assert finished.json()["run_id"] == receipt["run_id"]
    assert finished.json()["status"] == "completed"
    assert not process_once(settings, graph=graph)


def test_snapshot_cursor_replay_and_repeated_terminal(durable):
    client, settings, graph, cid, _ = durable
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]
    assert process_once(settings, graph=graph)
    base = f"/conversations/{cid}/runs/{rid}"
    snapshot = client.get(base + "/output")
    assert snapshot.status_code == 200, snapshot.text
    data = snapshot.json()
    assert data["status"] == "completed", data
    assert data["partial_reply"] == "streamed answer"
    assert data["result"]["reply"] == "streamed answer"
    streamed = events(client.get(base + "/stream"))
    assert [item[0] for item in streamed] == sorted({item[0] for item in streamed})
    assert (
        "".join(item[2]["delta"] for item in streamed if item[1] == "answer_delta")
        == "streamed answer"
    )
    assert streamed[-1] == (data["cursor"], "run_completed", data["result"])
    repeated = events(client.get(base + f"/stream?after={data['cursor']}"))
    assert repeated == [streamed[-1]]
    via_header = events(
        client.get(base + "/stream", headers={"Last-Event-ID": str(data["cursor"])})
    )
    assert via_header == repeated
    assert client.get(base + "/stream?after=-1").status_code == 400
    assert client.get(base + f"/stream?after={data['cursor'] + 1}").status_code == 400
    assert client.get(base + "/stream?after=0", headers={"Last-Event-ID": "1"}).status_code == 400


def test_queued_stop_prevents_generation(durable):
    client, settings, graph, cid, _ = durable
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]
    stopped = client.post(f"/conversations/{cid}/runs/{rid}/cancel")
    assert stopped.status_code == 200, stopped.text
    assert not process_once(settings, graph=graph)
    assert graph.calls == []
    snapshot = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
    assert snapshot["status"] == "cancelled"
    assert snapshot["result"] is None
    terminal = events(client.get(f"/conversations/{cid}/runs/{rid}/stream"))[-1]
    assert terminal[1] == "run_cancelled"
    assert terminal[2]["partial_reply_persisted"] is False
    audit = client.get(f"/conversations/{cid}/runs/{rid}/events")
    assert audit.status_code == 200
    assert "run_cancelled" in {item["event_type"] for item in audit.json()}


def test_lost_worker_is_stable_failed_state(durable):
    client, settings, graph, cid, _ = durable
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]
    job_id, _ = claim_job(settings.database_url, 60)
    with _sessionmaker_for_url(settings.database_url)() as db:
        db.execute(update(AgentRunModel).where(AgentRunModel.id == rid).values(status="running"))
        db.execute(
            update(DurableRunJob)
            .where(DurableRunJob.id == job_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    reap_lost_jobs(settings)
    snapshot = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
    assert snapshot["status"] == "failed"
    assert snapshot["error"]["code"] == "worker_lost"
    assert not process_once(settings, graph=graph)
    assert graph.calls == []
    terminal = events(client.get(f"/conversations/{cid}/runs/{rid}/stream"))[-1]
    assert terminal[1] == "run_failed"
    assert terminal[2]["code"] == "worker_lost"


def test_output_isolation_and_conversation_erasure(durable, monkeypatch):
    client, settings, graph, cid, _ = durable
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]
    assert process_once(settings, graph=graph)
    outsider = _client(monkeypatch)
    _signup_login(outsider, "durable-outsider@example.com")
    for suffix in ("output", "stream"):
        assert outsider.get(f"/conversations/{cid}/runs/{rid}/{suffix}").status_code == 404
    assert client.delete(f"/conversations/{cid}").status_code == 204
    with _sessionmaker_for_url(settings.database_url)() as db:
        assert db.scalar(select(DurableRunJob).where(DurableRunJob.run_id == rid)) is None
        assert db.get(DurableRunOutput, rid) is None
        assert db.scalar(select(DurableRunFrame).where(DurableRunFrame.run_id == rid)) is None
    outsider.close()


class PausingGraph(StreamingSpyGraph):
    """Pause after enough display text is committed, before the final result."""

    def __init__(self):
        super().__init__()
        self.paused = threading.Event()
        self.release = threading.Event()

    def stream(self, input, **kwargs):  # noqa: A002
        for frame in super().stream(input, **kwargs):
            if frame["type"] == "messages" and frame["data"][0].content == "streamed ":
                frame = {"type": "messages", "data": (_TextChunk("a" * 5000), {})}
            if frame["type"] == "updates" and "respond_general" in frame["data"]:
                self.paused.set()
                assert self.release.wait(10), "test did not release paused provider"
                frame["data"]["respond_general"]["reply"] = "a" * 5000 + "answer"
            yield frame


@pytest.mark.parametrize("stop", [False, True])
def test_listener_disconnect_does_not_own_execution_and_stop_does(durable, stop):
    client, settings, _, cid, user_id = durable
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]
    graph = PausingGraph()
    thread = threading.Thread(target=process_once, args=(settings,), kwargs={"graph": graph})
    thread.start()
    try:
        assert graph.paused.wait(10)
        snapshot = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
        assert snapshot["status"] == "running", snapshot
        assert snapshot["partial_reply"].startswith("a" * 4096)

        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        async def detach_listener():
            with _sessionmaker_for_url(settings.database_url)() as db:
                user = db.get(UserModel, user_id)
                session_id = db.scalar(
                    select(SessionModel.id).where(
                        SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None)
                    )
                )
                principal = Principal(
                    user_id=user_id, session_id=session_id, user_type=user.user_type
                )
                response = subscribe_output(
                    cid,
                    rid,
                    ConnectedRequest(),
                    principal,
                    db,
                    settings,
                    after="0",
                    last_event_id=None,
                )
                first = await anext(response.body_iterator)
                assert "id:" in first
                await response.body_iterator.aclose()

        asyncio.run(detach_listener())
        if stop:
            response = client.post(f"/conversations/{cid}/runs/{rid}/cancel")
            assert response.status_code == 200, response.text
    finally:
        graph.release.set()
        thread.join(10)
    assert not thread.is_alive()
    snapshot = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
    assert snapshot["status"] == ("cancelled" if stop else "completed"), snapshot
    assert len(graph.calls) == 1
    if stop:
        assert snapshot["result"] is None
    else:
        assert snapshot["result"]["reply"] == "a" * 5000 + "answer"


def test_replay_has_independent_idempotency_and_success_only_transcript_replacement(durable):
    client, settings, graph, cid, _ = durable
    admitted, _ = admit(client, cid)
    first_rid = admitted.json()["run_id"]
    assert process_once(settings, graph=graph)
    messages = client.get(f"/conversations/{cid}/messages").json()
    target = next(message["id"] for message in messages if message["role"] == "assistant")
    path = f"/conversations/{cid}/messages/{target}/replay/async"
    body = {"client_request_id": str(uuid.uuid4()), "request": {}}
    response = client.post(path, json=body)
    assert response.status_code == 202, response.text
    rid = response.json()["run_id"]
    assert rid != first_rid
    listed = next(
        item for item in client.get(f"/conversations/{cid}/runs").json() if item["run_id"] == rid
    )
    assert listed["client_request_id"] == body["client_request_id"]
    assert client.get(f"/conversations/{cid}/messages").json() == messages
    assert client.post(path, json=body).json()["run_id"] == rid
    assert process_once(settings, graph=graph)
    snapshot = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
    assert snapshot["status"] == "completed", snapshot
    assert snapshot["result"]["reply"] == "streamed answer"
    retried = client.post(path, json=body)
    assert retried.status_code == 202, retried.text
    assert retried.json()["run_id"] == rid
    assert retried.json()["status"] == "completed"
    messages = client.get(f"/conversations/{cid}/messages").json()
    assert len([message for message in messages if message["role"] == "assistant"]) == 1
    assert len(graph.calls) == 2


def test_concurrent_same_key_admits_exactly_one_job_and_prompt(durable):
    from concurrent.futures import ThreadPoolExecutor

    client, settings, graph, cid, _ = durable
    body = {"message": "Concurrent retry", "client_request_id": str(uuid.uuid4())}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: client.post(f"/conversations/{cid}/runs/async", json=body), range(4))
        )
    assert [response.status_code for response in responses] == [202] * 4
    assert len({response.json()["run_id"] for response in responses}) == 1
    assert len(client.get(f"/conversations/{cid}/messages").json()) == 1
    with _sessionmaker_for_url(settings.database_url)() as db:
        assert len(db.scalars(select(DurableRunJob)).all()) == 1
    assert process_once(settings, graph=graph)
    assert len(graph.calls) == 1


def test_expired_cursor_requires_fresh_projection(durable):
    client, settings, _, cid, _ = durable

    class LongAnswerGraph(StreamingSpyGraph):
        def stream(self, input, **kwargs):  # noqa: A002
            for frame in super().stream(input, **kwargs):
                if frame["type"] == "messages" and frame["data"][0].content == "streamed ":
                    frame = {"type": "messages", "data": (_TextChunk("a" * 100_000), {})}
                if frame["type"] == "updates" and "respond_general" in frame["data"]:
                    frame["data"]["respond_general"]["reply"] = "a" * 100_000 + "answer"
                yield frame

    graph = LongAnswerGraph()
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]
    bounded = settings.model_copy(update={"durable_run_max_frames": 10})
    assert process_once(bounded, graph=graph)
    with _sessionmaker_for_url(settings.database_url)() as db:
        assert db.get(DurableRunOutput, rid).min_cursor > 0
        assert (
            len(db.scalars(select(DurableRunFrame).where(DurableRunFrame.run_id == rid)).all())
            <= 10
        )
    base = f"/conversations/{cid}/runs/{rid}"
    response = client.get(base + "/stream?after=0")
    assert response.status_code == 410
    assert response.json()["code"] == "run_output_cursor_expired"
    snapshot = client.get(base + "/output").json()
    assert snapshot["result"]["reply"] == "a" * 100_000 + "answer"
    recovered = events(client.get(base + f"/stream?after={snapshot['cursor']}"))
    assert recovered[-1][1] == "run_completed"
    assert recovered[-1][2] == snapshot["result"]


def waiting_run(durable, *, execute=True, message="Summarize this document"):
    from langgraph.checkpoint.memory import InMemorySaver

    from my_agents.agents.general_assistant.graph import build_graph
    from my_agents.api.assistant import get_graph_runner
    from my_agents.persistence.langgraph import checkpoint_serializer

    from .test_conversations_api import _create_document, _create_knowledge_base

    client, settings, _, cid, _ = durable
    graph = build_graph(
        checkpointer=InMemorySaver(serde=checkpoint_serializer()),
        document_selection_hitl_enabled=True,
    )
    client.app.dependency_overrides[get_graph_runner] = lambda: graph
    kb_id = _create_knowledge_base(client, "Recoverable selection KB")
    for title in ("Alpha Source", "Beta Source"):
        document = _create_document(
            client,
            json={
                "title": title,
                "content": f"{title} authorized evidence.",
                "knowledge_base_id": kb_id,
            },
        )
        assert document.status_code == 201
        assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    response = client.post(
        f"/conversations/{cid}/runs/async",
        json={
            "message": message,
            "client_request_id": str(uuid.uuid4()),
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert response.status_code == 202, response.text
    rid = response.json()["run_id"]
    if execute:
        assert process_once(settings, graph=graph)
    return graph, rid


def test_waiting_recovery_async_resume_and_historical_control_replay(durable):
    client, settings, _, cid, _ = durable
    graph, rid = waiting_run(durable)
    base = f"/conversations/{cid}/runs/{rid}"
    pending = client.get(base + "/output").json()
    assert pending["status"] == "waiting_for_input", pending
    assert pending["interaction"]["type"] == "document_selection"
    waiting_events = events(client.get(base + "/stream"))
    assert waiting_events[-1][1] == "run_interrupted"
    assert waiting_events[-1][2]["execution_mode"] == "durable"
    assert waiting_events[-1][2]["interaction"] == pending["interaction"]
    listed = next(
        item for item in client.get(f"/conversations/{cid}/runs").json() if item["run_id"] == rid
    )
    assert listed["execution_mode"] == "durable"
    detail = client.get(base)
    assert detail.status_code == 200, detail.text
    assert detail.json()["execution_mode"] == "durable"

    with _sessionmaker_for_url(settings.database_url)() as db:
        output = db.get(DurableRunOutput, rid)
        output.terminal_cursor = output.terminal_event = output.terminal_data_json = None
        db.commit()
    repaired = client.get(base + "/output")
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["interaction"] == pending["interaction"]
    assert repaired.json()["cursor"] > pending["cursor"]
    repaired_events = events(client.get(base + f"/stream?after={repaired.json()['cursor']}"))
    assert repaired_events[-1][1] == "run_interrupted"
    assert repaired_events[-1][2]["interaction"] == pending["interaction"]

    body = {
        "client_request_id": str(uuid.uuid4()),
        "answer": {
            "schema_version": 2,
            "interaction_id": pending["interaction"]["interaction_id"],
            "type": "document_selection",
            "kind": "refine",
            "text": "Alpha Source",
        },
    }
    for suffix in ("/resume", "/resume/stream"):
        legacy = client.post(base + suffix, json=body["answer"])
        assert legacy.status_code == 409, legacy.text
        assert legacy.json()["code"] == "run_execution_mode_mismatch"
    resumed = client.post(base + "/resume/async", json=body)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["run_id"] == rid
    assert resumed.json()["status"] == "queued"
    queued = client.get(base + "/output").json()
    assert queued["status"] == "queued"
    assert queued["interaction"] is None
    assert queued["partial_reply"] == pending["partial_reply"]
    assert client.post(base + "/resume/async", json=body).json() == resumed.json()
    stale = client.post(
        base + "/resume/async", json={**body, "client_request_id": str(uuid.uuid4())}
    )
    assert stale.status_code == 409, stale.text
    assert process_once(settings, graph=graph)
    completed = client.get(base + "/output").json()
    assert completed["status"] == "completed", completed
    assert completed["result"]["reply"]
    retried = client.post(base + "/resume/async", json=body)
    assert retried.status_code == 202, retried.text
    assert retried.json()["run_id"] == rid
    assert retried.json()["status"] == "completed"
    remainder = events(client.get(base + "/stream?after=0"))
    assert remainder[-1][1] == "run_completed"
    assert not any(name == "run_interrupted" for _, name, _ in remainder)
    assert remainder[-1][2] == completed["result"]


def test_revoked_session_detaches_subscription_but_worker_finishes(durable):
    client, settings, _, cid, user_id = durable
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]
    graph = PausingGraph()
    thread = threading.Thread(target=process_once, args=(settings,), kwargs={"graph": graph})
    thread.start()
    try:
        assert graph.paused.wait(10)
        snapshot = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
        assert snapshot["status"] == "running"

        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        async def revoke_listener():
            with _sessionmaker_for_url(settings.database_url)() as db:
                user = db.get(UserModel, user_id)
                session_id = db.scalar(
                    select(SessionModel.id).where(
                        SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None)
                    )
                )
                principal = Principal(
                    user_id=user_id, session_id=session_id, user_type=user.user_type
                )
                response = subscribe_output(
                    cid,
                    rid,
                    ConnectedRequest(),
                    principal,
                    db,
                    settings,
                    after="0",
                    last_event_id=None,
                )
                last_id = 0
                while last_id < snapshot["cursor"]:
                    frame = await anext(response.body_iterator)
                    last_id = int(frame.split("\n", 1)[0].split(":", 1)[1])
                # Revoke after consuming the authorized batch, while the next
                # poll is waiting for new output from the independently paused worker.
                pending = asyncio.create_task(anext(response.body_iterator))
                await asyncio.sleep(settings.durable_run_poll_seconds * 2)
                with _sessionmaker_for_url(settings.database_url)() as revoker:
                    revoker.execute(
                        update(SessionModel)
                        .where(SessionModel.id == session_id)
                        .values(revoked_at=datetime.now(UTC))
                    )
                    revoker.commit()
                with pytest.raises(StopAsyncIteration):
                    await asyncio.wait_for(pending, 2)
                await response.body_iterator.aclose()

        asyncio.run(revoke_listener())
    finally:
        graph.release.set()
        thread.join(10)
    assert not thread.is_alive()
    with _sessionmaker_for_url(settings.database_url)() as db:
        assert db.get(AgentRunModel, rid).status == "completed"
        projection = json.loads(db.get(DurableRunOutput, rid).snapshot_json)
        assert projection["result"]["reply"] == "a" * 5000 + "answer"
    assert len(graph.calls) == 1


def test_legacy_run_rejects_async_resume(durable):
    from my_agents.api.assistant import get_graph_runner

    from .test_conversations_api import SpyGraph

    client, _, _, cid, _ = durable
    client.app.dependency_overrides[get_graph_runner] = lambda: SpyGraph()
    response = client.post(f"/conversations/{cid}/runs", json={"message": "Legacy answer"})
    assert response.status_code == 200, response.text
    rid = response.json()["run_id"]
    listed = next(
        item for item in client.get(f"/conversations/{cid}/runs").json() if item["run_id"] == rid
    )
    assert listed["execution_mode"] == "legacy"
    response = client.post(
        f"/conversations/{cid}/runs/{rid}/resume/async",
        json={
            "client_request_id": str(uuid.uuid4()),
            "answer": {
                "schema_version": 2,
                "interaction_id": str(uuid.uuid4()),
                "type": "document_selection",
                "kind": "refine",
                "text": "A clue",
            },
        },
    )
    assert response.status_code == 404, response.text


def test_expired_waiting_interaction_becomes_cancelled(durable):
    client, settings, _, cid, _ = durable
    _, rid = waiting_run(durable)
    base = f"/conversations/{cid}/runs/{rid}"
    pending = client.get(base + "/output").json()
    assert pending["status"] == "waiting_for_input"
    with _sessionmaker_for_url(settings.database_url)() as db:
        db.execute(
            update(AgentRunModel)
            .where(AgentRunModel.id == rid)
            .values(interaction_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    reap_lost_jobs(settings)
    expired = client.get(base + "/output").json()
    assert expired["status"] == "cancelled"
    assert expired["interaction"] is None
    assert expired["result"] is None
    terminal = events(client.get(base + f"/stream?after={pending['cursor']}"))[-1]
    assert terminal[1] == "run_cancelled"
    assert terminal[2]["partial_reply_persisted"] is False


def test_resume_before_previous_worker_cleanup_does_not_fail_new_phase(durable, monkeypatch):
    from my_agents import answer_worker

    client, settings, _, cid, _ = durable
    graph, rid = waiting_run(durable, execute=False)
    original_events = answer_worker._events
    interrupted_committed = threading.Event()
    release_prior_worker = threading.Event()

    def pause_after_interruption(*args, **kwargs):
        for frame in original_events(*args, **kwargs):
            yield frame
            if "event: run_interrupted\n" in frame:
                interrupted_committed.set()
                assert release_prior_worker.wait(10)

    monkeypatch.setattr(answer_worker, "_events", pause_after_interruption)
    prior = threading.Thread(target=process_once, args=(settings,), kwargs={"graph": graph})
    prior.start()
    try:
        assert interrupted_committed.wait(10)
        base = f"/conversations/{cid}/runs/{rid}"
        pending = client.get(base + "/output").json()
        assert pending["status"] == "waiting_for_input", pending
        resumed = client.post(
            base + "/resume/async",
            json={
                "client_request_id": str(uuid.uuid4()),
                "answer": {
                    "schema_version": 2,
                    "interaction_id": pending["interaction"]["interaction_id"],
                    "type": "document_selection",
                    "kind": "refine",
                    "text": "Alpha Source",
                },
            },
        )
        assert resumed.status_code == 202, resumed.text
        assert resumed.json()["status"] == "queued"
    finally:
        release_prior_worker.set()
        prior.join(10)
    assert not prior.is_alive()
    before_execution = client.get(base + "/output").json()
    assert before_execution["status"] == "queued", before_execution
    assert process_once(settings, graph=graph)
    finished = client.get(base + "/output").json()
    assert finished["status"] == "completed", finished


def test_worker_readiness_controls_admission_but_preserves_existing_output(durable):
    from sqlalchemy import delete

    from my_agents.conversations.durable_models import DurableWorkerHeartbeat

    client, settings, graph, cid, _ = durable
    with _sessionmaker_for_url(settings.database_url)() as db:
        db.execute(delete(DurableWorkerHeartbeat))
        db.commit()
    assert client.get("/health").json()["frontend_config"]["durable_runs"] == {
        "enabled": True,
        "ready": False,
    }
    unavailable, _ = admit(client, cid)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "durable_runs_unavailable"
    assert client.get(f"/conversations/{cid}/messages").json() == []
    legacy = client.post(f"/conversations/{cid}/runs/stream", json={"message": "Legacy stream"})
    assert legacy.status_code == 200
    assert "event: run_completed" in legacy.text
    record_worker_heartbeat(settings.database_url, "ready-again")
    assert client.get("/health").json()["frontend_config"]["durable_runs"] == {
        "enabled": True,
        "ready": True,
    }
    admitted, _ = admit(client, cid)
    assert admitted.status_code == 202
    rid = admitted.json()["run_id"]
    assert process_once(settings, graph=graph)
    with _sessionmaker_for_url(settings.database_url)() as db:
        db.execute(delete(DurableWorkerHeartbeat))
        db.commit()
    assert client.get("/health").json()["frontend_config"]["durable_runs"] == {
        "enabled": True,
        "ready": False,
    }
    unavailable, _ = admit(client, cid)
    assert unavailable.status_code == 503
    snapshot = client.get(f"/conversations/{cid}/runs/{rid}/output")
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "completed"
    assert events(client.get(f"/conversations/{cid}/runs/{rid}/stream"))[-1][1] == "run_completed"


def test_disabled_feature_preserves_legacy_and_durable_recovery(durable):
    client, settings, graph, cid, _ = durable
    admitted, body = admit(client, cid)
    assert admitted.status_code == 202
    rid = admitted.json()["run_id"]
    assert process_once(settings, graph=graph)
    # A fresh worker heartbeat must not override the operator's disabled flag.
    client.app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"durable_runs_enabled": False}
    )
    assert client.get("/health").json()["frontend_config"]["durable_runs"] == {
        "enabled": False,
        "ready": False,
    }
    before = client.get(f"/conversations/{cid}/messages").json()
    unavailable, _ = admit(client, cid)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "durable_runs_unavailable"
    assert client.get(f"/conversations/{cid}/messages").json() == before
    repeated = client.post(f"/conversations/{cid}/runs/async", json=body)
    assert repeated.status_code == 202
    assert repeated.json()["run_id"] == rid
    assert repeated.json()["status"] == "completed"
    assert client.get(f"/conversations/{cid}/runs/{rid}/output").json()["status"] == "completed"
    assert events(client.get(f"/conversations/{cid}/runs/{rid}/stream"))[-1][1] == "run_completed"
    legacy = client.post(
        f"/conversations/{cid}/runs/stream", json={"message": "Legacy remains available"}
    )
    assert legacy.status_code == 200
    assert "event: run_completed" in legacy.text


def test_guest_async_attachment_rejected_before_job_creation(durable):
    from fastapi.testclient import TestClient

    from my_agents.api.document_workspace import get_document_workspace_provider
    from my_agents.auth.service import AuthService

    from .test_document_workspace import FakeDocumentWorkspaceProvider

    client, settings, graph, _, _ = durable
    guest_settings = settings.model_copy(
        update={"guest_access_enabled": True, "document_workspace_enabled": True}
    )
    client.app.dependency_overrides[get_settings] = lambda: guest_settings
    client.app.dependency_overrides[get_document_workspace_provider] = lambda: (
        FakeDocumentWorkspaceProvider()
    )
    with _sessionmaker_for_url(settings.database_url)() as db:
        issued = AuthService(db, guest_settings=guest_settings).issue_guest_access_code(
            email="durable-guest@example.com", ttl=timedelta(minutes=15)
        )
    with TestClient(client.app) as guest:
        login = guest.post("/auth/guest/login", json={"code": issued.code})
        assert login.status_code == 200, login.text
        created = guest.post("/conversations", json={"title": "Guest conversation"})
        assert created.status_code == 201, created.text
        cid = created.json()["id"]
        rejected = guest.post(
            f"/conversations/{cid}/runs/async",
            json={
                "message": "Read attachment",
                "client_request_id": str(uuid.uuid4()),
                "attachment_ids": [str(uuid.uuid4())],
            },
        )
        assert rejected.status_code == 403, rejected.text
        assert rejected.json()["code"] == "guest_document_workspace_forbidden"
        assert guest.get(f"/conversations/{cid}/messages").json() == []
        with _sessionmaker_for_url(settings.database_url)() as db:
            assert db.scalar(select(DurableRunJob)) is None
        accepted, _ = admit(guest, cid, message="Guest text remains available")
        assert accepted.status_code == 202, accepted.text
        rid = accepted.json()["run_id"]
        assert process_once(guest_settings, graph=graph)
        output = guest.get(f"/conversations/{cid}/runs/{rid}/output").json()
        assert output["status"] == "completed", output
        assert output["result"]["assistant_model"] == guest_settings.guest_assistant_model


def test_async_document_workspace_uses_mocked_provider_and_preserves_artifacts(durable):
    from io import BytesIO

    from my_agents.agents.general_assistant.graph import build_graph
    from my_agents.api.document_workspace import get_document_workspace_provider

    from .test_document_workspace import FakeDocumentWorkspaceProvider

    client, settings, _, cid, _ = durable
    workspace_settings = settings.model_copy(update={"document_workspace_enabled": True})
    provider = FakeDocumentWorkspaceProvider()
    client.app.dependency_overrides[get_settings] = lambda: workspace_settings
    client.app.dependency_overrides[get_document_workspace_provider] = lambda: provider
    uploaded = client.post(
        f"/conversations/{cid}/attachments",
        files={
            "file": (
                "analysis.xlsx",
                BytesIO(b"xlsx-input"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
        data={"provider_consent": "true"},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    admitted = client.post(
        f"/conversations/{cid}/runs/async",
        json={
            "message": "Summarize the file and create a new spreadsheet",
            "client_request_id": str(uuid.uuid4()),
            "attachment_ids": [attachment["id"]],
            "reasoning_mode": "pro",
            "reasoning_effort": "max",
        },
    )
    assert admitted.status_code == 202, admitted.text
    rid = admitted.json()["run_id"]
    assert provider.reasoning is None
    assert process_once(workspace_settings, graph=build_graph(), provider=provider)
    output = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
    assert output["status"] == "completed", output
    result = output["result"]
    assert result["reply"] == "분석한 스프레드시트를 만들었습니다."
    assert [item["id"] for item in result["attachments"]] == [attachment["id"]]
    assert result["artifacts"][0]["filename"] == "analysis.xlsx"
    assert provider.reasoning == ("pro", "max")
    assert client.get(result["artifacts"][0]["download_url"]).content == b"artifact-bytes"


def test_waiting_output_filters_documents_removed_after_interruption(durable):
    from my_agents.api.conversations.interactions import interrupted_run_response
    from my_agents.conversations.durable_store import append_frame
    from my_agents.knowledge.models import DocumentModel

    client, settings, _, cid, _ = durable
    _, rid = waiting_run(durable)
    base = f"/conversations/{cid}/runs/{rid}"
    with _sessionmaker_for_url(settings.database_url)() as db:
        # Seed a valid ranked shortlist, as the selection planner can produce,
        # before revoking one of its documents. Both projection and frame cache it.
        run = db.get(AgentRunModel, rid)
        interaction = json.loads(run.interaction_payload_json)
        documents = db.scalars(select(DocumentModel).order_by(DocumentModel.id)).all()
        interaction["options"] = [
            {
                "document_id": document.id,
                "title": document.title,
                "knowledge_base_id": document.knowledge_base_id,
                "match_confidence": "medium",
                "match_reason_code": "partial_title",
            }
            for document in documents
        ]
        interaction["option_count"] = 2
        interaction["reason_code"] = "ambiguous_document_reference"
        run.interaction_payload_json = json.dumps(interaction)
        db.flush()
        append_frame(
            db, rid, "run_interrupted", interrupted_run_response(run).model_dump(mode="json")
        )
        db.commit()
    before = client.get(base + "/output").json()
    assert before["status"] == "waiting_for_input", before
    options = before["interaction"]["options"]
    assert len(options) == 2, before["interaction"]
    removed_id = options[0]["document_id"]
    assert client.delete(f"/documents/{removed_id}").status_code == 204
    after = client.get(base + "/output").json()
    assert removed_id not in {option["document_id"] for option in after["interaction"]["options"]}
    assert after["interaction"]["option_count"] == 1
    assert after["interaction"]["library_count"] == 1
    terminal = events(client.get(base + f"/stream?after={before['cursor']}"))[-1]
    assert terminal[1] == "run_interrupted"
    assert removed_id not in {
        option["document_id"] for option in terminal[2]["interaction"]["options"]
    }


def replay_with_suffix(durable):
    client, settings, graph, cid, _ = durable
    original_runs = []
    for message in ("First question", "Later question"):
        admitted, _ = admit(client, cid, message=message)
        assert admitted.status_code == 202
        original_runs.append(admitted.json()["run_id"])
        assert process_once(settings, graph=graph)
    before = client.get(f"/conversations/{cid}/messages").json()
    target = next(message["id"] for message in before if message["role"] == "assistant")
    admitted = client.post(
        f"/conversations/{cid}/messages/{target}/replay/async",
        json={
            "client_request_id": str(uuid.uuid4()),
            "request": {},
        },
    )
    assert admitted.status_code == 202, admitted.text
    return original_runs, admitted.json()["run_id"], before


def test_replay_prunes_atomically_before_a_following_admission(durable, monkeypatch):
    from my_agents.api.conversations.endpoints import replay

    client, settings, graph, cid, _ = durable
    original_runs, replay_id, _ = replay_with_suffix(durable)
    persisted = threading.Event()
    release_replay = threading.Event()
    complete = replay.persist_completed_run

    def pause_after_completion(**kwargs):
        result = complete(**kwargs)
        persisted.set()
        assert release_replay.wait(10)
        return result

    monkeypatch.setattr(replay, "persist_completed_run", pause_after_completion)
    thread = threading.Thread(target=process_once, args=(settings,), kwargs={"graph": graph})
    thread.start()
    try:
        assert persisted.wait(10)
        # Completion and old-suffix removal are already visible together, even
        # though the replay generator has not reached its old pruning location.
        transcript = client.get(f"/conversations/{cid}/messages").json()
        assert [(item["role"], item["content"]) for item in transcript] == [
            ("user", "First question"),
            ("assistant", "streamed answer"),
        ]
        admitted, _ = admit(client, cid, message="After regeneration completed")
        assert admitted.status_code == 202, admitted.text
        next_id = admitted.json()["run_id"]
    finally:
        release_replay.set()
        thread.join(10)
    assert not thread.is_alive()
    with _sessionmaker_for_url(settings.database_url)() as db:
        assert db.get(AgentRunModel, replay_id).status == "completed"
        assert db.get(AgentRunModel, next_id).status == "queued"
        assert db.scalar(select(DurableRunJob).where(DurableRunJob.run_id == next_id)) is not None
        assert all(db.get(AgentRunModel, old_id) is None for old_id in original_runs)
    assert process_once(settings, graph=graph)
    assert client.get(f"/conversations/{cid}/runs/{next_id}/output").json()["status"] == "completed"
    transcript = client.get(f"/conversations/{cid}/messages").json()
    assert [item["content"] for item in transcript if item["role"] == "user"] == [
        "First question",
        "After regeneration completed",
    ]


def test_replay_pruning_failure_rolls_back_completion_and_preserves_suffix(durable, monkeypatch):
    from my_agents.api.conversations import transcripts

    client, settings, graph, cid, _ = durable
    original_runs, replay_id, before = replay_with_suffix(durable)
    prune = transcripts.prune_conversation_from_message

    def fail_after_deletes(*args, **kwargs):
        assert kwargs["commit"] is False
        prune(*args, **kwargs)
        raise RuntimeError("simulated failure before atomic completion commit")

    monkeypatch.setattr(transcripts, "prune_conversation_from_message", fail_after_deletes)
    assert process_once(settings, graph=graph)
    output = client.get(f"/conversations/{cid}/runs/{replay_id}/output").json()
    assert output["status"] == "failed", output
    assert output["result"] is None
    assert client.get(f"/conversations/{cid}/messages").json() == before
    with _sessionmaker_for_url(settings.database_url)() as db:
        assert all(db.get(AgentRunModel, old_id).status == "completed" for old_id in original_runs)
        assert all(db.get(DurableRunOutput, old_id) is not None for old_id in original_runs)


def test_postcommit_serializer_failure_delivers_canonical_completion(durable, monkeypatch):
    from my_agents.api.conversations import run_lifecycle

    client, settings, graph, cid, _ = durable
    admitted, _ = admit(client, cid)
    rid = admitted.json()["run_id"]

    def fail_transient_response(**kwargs):
        raise RuntimeError("simulated response construction failure after canonical commit")

    monkeypatch.setattr(run_lifecycle, "completed_run_response", fail_transient_response)
    assert process_once(settings, graph=graph)
    base = f"/conversations/{cid}/runs/{rid}"
    output = client.get(base + "/output").json()
    assert output["status"] == "completed", output
    assert output["error"] is None
    assert output["result"]["reply"] == "streamed answer"
    delivered = events(client.get(base + "/stream"))
    assert delivered[-1][1] == "run_completed"
    assert delivered[-1][2] == output["result"]
    assert not any(name == "run_failed" for _, name, _ in delivered)
    messages = client.get(f"/conversations/{cid}/messages").json()
    assert len([item for item in messages if item["role"] == "assistant"]) == 1
    assert len(graph.calls) == 1
