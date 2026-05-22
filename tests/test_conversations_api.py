"""Server-owned conversation and product run API tests."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.conversations.models import AgentRunModel, RunStatus
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class SpyGraph:
    """Graph spy that records app-owned message state passed to run endpoint."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict) -> dict:  # noqa: A002 - matches LangGraph API
        self.calls.append(input)
        messages = input["messages"]
        return {
            "reply": f"saw {len(messages)} messages",
            "route": RouteDecision(label="general_assistant", explanation="spy route"),
        }


class _TextChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class StreamingSpyGraph:
    """Graph spy that emits assistant text chunks before the final graph update."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict) -> dict:  # noqa: A002, ARG002 - matches LangGraph API
        raise AssertionError("streaming endpoint should use graph.stream when available")

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002, ARG002 - matches LangGraph API
        self.calls.append(input)
        yield {"type": "messages", "data": (_TextChunk("streamed "), {})}
        yield {"type": "messages", "data": (_TextChunk("answer"), {})}
        yield {
            "type": "updates",
            "data": {
                "classify_request": {
                    "route": RouteDecision(label="general_assistant", explanation="spy route")
                },
                "respond_general": {"reply": "streamed answer"},
            },
        }


class CancellingStreamingGraph:
    """Graph spy that simulates a cancel request while the stream is active."""

    def invoke(self, input: dict) -> dict:  # noqa: A002, ARG002 - matches LangGraph API
        raise AssertionError("streaming endpoint should use graph.stream when available")

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002, ARG002 - matches LangGraph API
        _mark_latest_running_run_cancelling(input["conversation_id"])
        yield {"type": "messages", "data": (_TextChunk("cancelled text"), {})}


class FailingGraph:
    """Graph spy that forces the product run failure path."""

    def invoke(self, input: dict) -> dict:  # noqa: A002, ARG002 - matches LangGraph API
        raise RuntimeError("private provider failure: do not leak raw prompt")


def _client(
    monkeypatch,  # noqa: ANN001
    graph: SpyGraph | StreamingSpyGraph | FailingGraph | None = None,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    app = create_app()
    if graph is not None:
        app.dependency_overrides[get_graph_runner] = lambda: graph
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _signup_login(client: TestClient, email: str) -> str:
    password = "correct horse battery staple"
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def _create_knowledge_base(client: TestClient, name: str = "Test KB") -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_document(client: TestClient, *, json: dict):  # noqa: ANN201
    payload = dict(json)
    payload.setdefault("knowledge_base_id", _create_knowledge_base(client))
    return client.post("/documents", json=payload)


def test_conversation_run_uses_server_owned_history(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "history@example.com")
    conversation = client.post("/conversations", json={"title": "History"})
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]

    first = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Hello"})
    second = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Continue"})

    assert first.status_code == 200
    assert first.json()["reply"] == "saw 1 messages"
    assert second.status_code == 200
    assert second.json()["reply"] == "saw 3 messages"
    assert graph.calls[0]["conversation_id"] == conversation_id
    assert graph.calls[0]["principal_id"]
    assert [message.content for message in graph.calls[1]["messages"]] == [
        "Hello",
        "saw 1 messages",
        "Continue",
    ]
    assert first.json()["retrieval_route"] == "no_retrieval"
    assert first.json()["answer_mode"] == "general_knowledge"


def test_general_prompt_skips_retrieval_service(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "no-retrieval@example.com")
    conversation_id = client.post("/conversations", json={"title": "No retrieval"}).json()["id"]

    def fail_retrieve(self, **kwargs):  # noqa: ANN001, ANN202, ARG001
        raise AssertionError("general prompt should not call RetrievalService.retrieve")

    monkeypatch.setattr(RetrievalService, "retrieve", fail_retrieve)

    response = client.post(
        f"/conversations/{conversation_id}/runs", json={"message": "RAG가 뭐야?"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval_route"] == "no_retrieval"
    assert payload["answer_mode"] == "general_knowledge"
    assert payload["citations"] == []
    assert graph.calls[-1]["retrieved_context"] == []


def test_optional_retrieval_without_context_answers_generally(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "optional-empty@example.com")
    conversation_id = client.post("/conversations", json={"title": "Optional empty"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "우리 서비스 인증 로직 어떻게 정리하면 좋을까?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval_route"] == "retrieval_optional"
    assert payload["answer_mode"] == "general_knowledge"
    assert payload["citations"] == []
    assert graph.calls[-1]["answer_mode"] == "general_knowledge"


def test_optional_retrieval_with_relevant_context_uses_mixed_mode(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "optional-mixed@example.com")
    document = _create_document(
        client,
        json={
            "title": "Service Auth Notes",
            "content": "우리 서비스 인증 로직은 세션 쿠키와 CSRF 토큰으로 정리합니다.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Optional mixed"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "우리 서비스 인증 로직 어떻게 정리하면 좋을까?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval_route"] == "retrieval_optional"
    assert payload["answer_mode"] == "mixed"
    assert payload["citations"]
    assert graph.calls[-1]["answer_mode"] == "mixed"
    assert graph.calls[-1]["retrieved_context"]


def test_ambiguous_document_scope_returns_clarification_without_graph(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "clarify-docs@example.com")
    kb_id = _create_personal_knowledge_base(client, "Clarify Docs KB")
    for title in ("Doc A", "Doc B"):
        response = _create_document(client, json={"title": title, "content": f"{title} content"})
        assert response.status_code == 201
    conversation_id = client.post("/conversations", json={"title": "Clarify"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "이 문서 기준으로 개선점을 알려줘"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval_route"] == "clarification_required"
    assert payload["answer_mode"] == "general_knowledge"
    assert payload["citations"] == []
    assert "which document or file" in payload["reply"]
    assert graph.calls == []


def test_conversation_messages_can_be_listed_in_server_owned_order(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "messages@example.com")
    conversation_id = client.post("/conversations", json={"title": "Transcript"}).json()["id"]

    note = client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Remember this first note."},
    )
    run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Use the note now."},
    )
    transcript = client.get(f"/conversations/{conversation_id}/messages")

    assert note.status_code == 201
    assert run.status_code == 200
    assert transcript.status_code == 200
    assert [(message["role"], message["content"]) for message in transcript.json()] == [
        ("user", "Remember this first note."),
        ("user", "Use the note now."),
        ("assistant", "saw 2 messages"),
    ]


def test_conversation_access_is_scoped_to_owner_or_group_member(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    member = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "conv-owner@example.com")
    member_id = _signup_login(member, "conv-member@example.com")
    _signup_login(outsider, "conv-outsider@example.com")

    group_id = owner.post("/groups", json={"name": "Conversation Group"}).json()["id"]
    owner.post(f"/groups/{group_id}/members", json={"user_id": member_id, "role": "viewer"})
    conversation_id = owner.post(
        "/conversations",
        json={"title": "Group Conversation", "group_id": group_id},
    ).json()["id"]

    assert member.get(f"/conversations/{conversation_id}").status_code == 200
    assert outsider.get(f"/conversations/{conversation_id}").status_code == 404


def test_conversation_messages_are_hidden_from_unauthorized_users(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "messages-owner@example.com")
    _signup_login(outsider, "messages-outsider@example.com")
    conversation_id = owner.post("/conversations", json={"title": "Private"}).json()["id"]
    assert (
        owner.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "private transcript"},
        ).status_code
        == 201
    )

    response = outsider.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 404
    assert "private transcript" not in response.text


def test_conversation_runs_can_be_listed_without_event_details(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "runs@example.com")
    conversation_id = client.post("/conversations", json={"title": "Runs"}).json()["id"]

    first = client.post(f"/conversations/{conversation_id}/runs", json={"message": "First"})
    second = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Second"})
    runs = client.get(f"/conversations/{conversation_id}/runs")

    assert first.status_code == 200
    assert second.status_code == 200
    assert runs.status_code == 200
    payload = runs.json()
    assert {run["run_id"] for run in payload} == {
        first.json()["run_id"],
        second.json()["run_id"],
    }
    assert all(run["status"] == "completed" for run in payload)
    assert all(run["route_label"] == "general_assistant" for run in payload)
    assert all("reply" not in run for run in payload)


def test_completed_conversation_run_detail_survives_refresh(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "run-detail@example.com")
    conversation_id = client.post("/conversations", json={"title": "Run detail"}).json()["id"]
    run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Persist this run"},
    )
    assert run.status_code == 200
    run_payload = run.json()

    detail = client.get(f"/conversations/{conversation_id}/runs/{run_payload['run_id']}")

    assert detail.status_code == 200
    assert detail.json() == run_payload


def test_failed_conversation_run_detail_returns_conflict(monkeypatch) -> None:  # noqa: ANN001
    client = _client(
        monkeypatch,
        FailingGraph(),
        raise_server_exceptions=False,
    )
    _signup_login(client, "failed-detail@example.com")
    conversation_id = client.post("/conversations", json={"title": "Failed detail"}).json()["id"]
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "This run fails"},
    )
    assert response.status_code == 502
    failed_run = client.get(f"/conversations/{conversation_id}/runs").json()[0]

    detail = client.get(f"/conversations/{conversation_id}/runs/{failed_run['run_id']}")

    assert detail.status_code == 409
    assert detail.json()["detail"] == "run is not completed"


def test_streaming_conversation_run_emits_events_and_persists_result(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "stream@example.com")
    conversation_id = client.post("/conversations", json={"title": "Stream"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "Stream this"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(response.read().decode())

    assert [event["event"] for event in events] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "answer_delta",
        "answer_delta",
        "answer_delta",
        "answer_composed",
        "run_completed",
    ]
    completed = events[-1]["data"]
    assert completed["conversation_id"] == conversation_id
    assert completed["reply"] == "saw 1 messages"
    assert completed["handled_by"] == "personal_assistant_graph"
    assert completed["route"]["label"] == "general_assistant"
    assert graph.calls[0]["conversation_id"] == conversation_id

    transcript = client.get(f"/conversations/{conversation_id}/messages")
    run_events = client.get(f"/conversations/{conversation_id}/runs/{completed['run_id']}/events")

    assert [(message["role"], message["content"]) for message in transcript.json()] == [
        ("user", "Stream this"),
        ("assistant", "saw 1 messages"),
    ]
    assert [event["event_type"] for event in run_events.json()] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "answer_composed",
    ]


def test_streaming_conversation_run_emits_answer_deltas_before_completion(monkeypatch) -> None:  # noqa: ANN001
    graph = StreamingSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "stream-delta@example.com")
    conversation_id = client.post("/conversations", json={"title": "Stream deltas"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "Stream actual assistant text"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    event_names = [event["event"] for event in events]
    delta_events = [event for event in events if event["event"] == "answer_delta"]
    completed = events[-1]["data"]

    assert event_names == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "answer_delta",
        "answer_delta",
        "answer_composed",
        "run_completed",
    ]
    assert len(delta_events) == 2
    assert [event["data"]["sequence"] for event in delta_events] == [1, 2]
    assert "".join(event["data"]["delta"] for event in delta_events) == "streamed answer"
    assert event_names.index("answer_delta") < event_names.index("run_completed")
    assert completed["reply"] == "streamed answer"
    assert graph.calls[0]["conversation_id"] == conversation_id

    transcript = client.get(f"/conversations/{conversation_id}/messages").json()
    runs = client.get(f"/conversations/{conversation_id}/runs").json()

    assert [(message["role"], message["content"]) for message in transcript] == [
        ("user", "Stream actual assistant text"),
        ("assistant", "streamed answer"),
    ]
    assert len(runs) == 1
    assert runs[0]["run_id"] == completed["run_id"]


def test_failed_streaming_conversation_run_persists_redacted_error(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, FailingGraph())
    _signup_login(client, "failed-stream@example.com")
    conversation_id = client.post("/conversations", json={"title": "Failed stream"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "Do not leak streamed text"},
    ) as response:
        assert response.status_code == 200
        body = response.read().decode()
        events = _parse_sse(body)

    assert [event["event"] for event in events] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "run_failed",
        "run_error",
    ]
    assert events[-2]["data"]["safe_error_type"] == "RuntimeError"
    assert events[-1]["data"]["status_code"] == 502
    assert "Do not leak streamed text" not in body
    assert "private provider failure" not in body

    runs = client.get(f"/conversations/{conversation_id}/runs").json()
    failed_run = runs[0]
    run_events = client.get(f"/conversations/{conversation_id}/runs/{failed_run['run_id']}/events")

    assert failed_run["status"] == "failed"
    assert [event["event_type"] for event in run_events.json()] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "run_failed",
    ]
    assert run_events.json()[-1]["payload"] == {"safe_error_type": "RuntimeError"}
    assert "Do not leak streamed text" not in run_events.text


def test_failed_conversation_run_is_persisted_with_redacted_event(monkeypatch) -> None:  # noqa: ANN001
    client = _client(
        monkeypatch,
        FailingGraph(),
        raise_server_exceptions=False,
    )
    _signup_login(client, "failed-run@example.com")
    conversation_id = client.post("/conversations", json={"title": "Failure"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Do not leak this user text"},
    )
    runs = client.get(f"/conversations/{conversation_id}/runs")

    assert response.status_code == 502
    assert response.json() == {"detail": "conversation run failed"}
    assert runs.status_code == 200
    failed_run = runs.json()[0]
    assert failed_run["status"] == "failed"
    assert failed_run["route_label"] is None

    events = client.get(f"/conversations/{conversation_id}/runs/{failed_run['run_id']}/events")

    assert events.status_code == 200
    event_payload = events.json()
    assert [event["event_type"] for event in event_payload] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "run_failed",
    ]
    assert event_payload[-1]["payload"] == {"safe_error_type": "RuntimeError"}
    assert "Do not leak this user text" not in events.text
    assert "private provider failure" not in events.text


def test_cancel_run_endpoint_marks_running_run_cancelling(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "cancel-endpoint@example.com")
    conversation_id = client.post("/conversations", json={"title": "Cancel endpoint"}).json()["id"]
    run_id = _create_running_run(conversation_id=conversation_id, user_id=user_id)

    response = client.post(f"/conversations/{conversation_id}/runs/{run_id}/cancel")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "status": "cancelling",
    }
    events = client.get(f"/conversations/{conversation_id}/runs/{run_id}/events")
    assert events.status_code == 200
    assert events.json()[-1]["event_type"] == "run_cancel_requested"


def test_active_run_rejects_parallel_conversation_run(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    user_id = _signup_login(client, "active-run@example.com")
    conversation_id = client.post("/conversations", json={"title": "Active run"}).json()["id"]
    _create_running_run(conversation_id=conversation_id, user_id=user_id)

    response = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Second"})

    assert response.status_code == 409
    assert response.json() == {"detail": "conversation run already active"}
    assert graph.calls == []


def test_streaming_cancelled_run_does_not_persist_partial_assistant(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, CancellingStreamingGraph())
    _signup_login(client, "stream-cancel@example.com")
    conversation_id = client.post("/conversations", json={"title": "Stream cancel"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "Cancel this stream"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    assert [event["event"] for event in events] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "run_cancelled",
    ]
    run_id = events[0]["data"]["run_id"]
    assert events[-1]["data"] == {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "status": "cancelled",
        "partial_reply_persisted": False,
    }

    transcript = client.get(f"/conversations/{conversation_id}/messages").json()
    runs = client.get(f"/conversations/{conversation_id}/runs").json()
    run_events = client.get(f"/conversations/{conversation_id}/runs/{run_id}/events").json()

    assert [(message["role"], message["content"]) for message in transcript] == [
        ("user", "Cancel this stream"),
    ]
    assert runs[0]["status"] == "cancelled"
    assert [event["event_type"] for event in run_events] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "run_cancelled",
    ]
    detail = client.get(f"/conversations/{conversation_id}/runs/{run_id}")
    assert detail.status_code == 409


def test_legacy_assistant_chat_remains_dev_surface_without_product_run_fields(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)

    response = client.post("/assistant/chat", json={"message": "Hello", "history": []})

    assert response.status_code == 200
    payload = response.json()
    assert payload["handled_by"] == "personal_assistant_graph"
    assert "run_id" not in payload
    assert "citations" not in payload


def _create_running_run(*, conversation_id: str, user_id: str) -> str:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = AgentRunModel(
            conversation_id=conversation_id,
            user_id=user_id,
            status=RunStatus.RUNNING.value,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        session_generator.close()


def _mark_latest_running_run_cancelling(conversation_id: str) -> None:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = db.scalar(
            select(AgentRunModel)
            .where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.status == RunStatus.RUNNING.value,
            )
            .order_by(AgentRunModel.created_at.desc())
        )
        assert run is not None
        run.status = RunStatus.CANCELLING.value
        db.commit()
    finally:
        session_generator.close()


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_event in body.strip().split("\n\n"):
        event_name = ""
        data = ""
        for line in raw_event.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            if line.startswith("data: "):
                data = line.removeprefix("data: ")
        events.append({"event": event_name, "data": json.loads(data)})
    return events
