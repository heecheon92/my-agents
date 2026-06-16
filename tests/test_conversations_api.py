"""Server-owned conversation and product run API tests."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.api.conversations import retrieval_context as retrieval_context_module
from my_agents.api.conversations.endpoints import replay as replay_endpoint
from my_agents.api.conversations.endpoints.stream import conversation_run_events
from my_agents.conversations.models import (
    AgentEventModel,
    AgentEventType,
    AgentRunModel,
    ConversationModel,
    MessageModel,
    RunStatus,
)
from my_agents.conversations.schemas import ConversationRunRequest
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import CitationModel
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email
from .rag_spy_helpers import rag_update_for_spy


class SpyGraph:
    """Graph spy that records app-owned message state passed to run endpoint."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict, **kwargs: Any) -> dict:  # noqa: A002 - matches LangGraph API
        rag_update = rag_update_for_spy(input, kwargs)
        self.calls.append({**input, **rag_update})
        messages = input["messages"]
        return {
            **rag_update,
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

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002 - matches LangGraph API
        rag_update = rag_update_for_spy(input, kwargs)
        self.calls.append({**input, **rag_update})
        yield {"type": "updates", "data": {"retrieve_rag_context": rag_update}}
        yield {"type": "messages", "data": (_TextChunk("streamed "), {})}
        yield {"type": "messages", "data": (_TextChunk("answer"), {})}
        yield {
            "type": "updates",
            "data": {
                "classify_request": {
                    "route": RouteDecision(label="general_assistant", explanation="spy route")
                },
                "respond_general": {"reply": "streamed answer", **rag_update},
            },
        }


class CancellingStreamingGraph:
    """Graph spy that simulates a cancel request while the stream is active."""

    def invoke(self, input: dict) -> dict:  # noqa: A002, ARG002 - matches LangGraph API
        raise AssertionError("streaming endpoint should use graph.stream when available")

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002 - matches LangGraph API
        yield {
            "type": "updates",
            "data": {"retrieve_rag_context": rag_update_for_spy(input, kwargs)},
        }
        _mark_latest_running_run_cancelling(input["conversation_id"])
        yield {"type": "messages", "data": (_TextChunk("cancelled text"), {})}


class FailingGraph:
    """Graph spy that forces the product run failure path."""

    def invoke(self, input: dict) -> dict:  # noqa: A002, ARG002 - matches LangGraph API
        raise RuntimeError("private provider failure: do not leak raw prompt")

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002 - matches LangGraph API
        yield {
            "type": "updates",
            "data": {"retrieve_rag_context": rag_update_for_spy(input, kwargs)},
        }
        raise RuntimeError("private provider failure: do not leak raw prompt")


class MemoryUpdateThenFailingGraph:
    """Graph spy that emits memory provenance before a later provider failure."""

    def invoke(self, input: dict, **kwargs: Any) -> dict:  # noqa: A002, ARG002
        raise AssertionError("sync run should collect update stream state when available")

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002
        yield {
            "type": "updates",
            "data": {"retrieve_rag_context": rag_update_for_spy(input, kwargs)},
        }
        yield {
            "type": "updates",
            "data": {
                "retrieve_memory": {
                    "memory_context": [
                        {
                            "id": "memory-internal-1",
                            "category": "stable_preference",
                            "provenance_type": "manual",
                            "source_conversation_id": "conversation-internal-1",
                            "source_message_id": "message-internal-1",
                            "source_run_id": "run-internal-1",
                            "source_document_id": "document-internal-1",
                            "content": "User prefers concise answers",
                        }
                    ],
                    "source_conflicts": [],
                }
            },
        }
        raise RuntimeError("provider failed after memory recall")


def _client(
    monkeypatch,  # noqa: ANN001
    graph: SpyGraph | StreamingSpyGraph | FailingGraph | MemoryUpdateThenFailingGraph | None = None,
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
    signup = client.post(
        "/auth/signup", json={"email": email, "nickname": "Test User", "password": password}
    )
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


def test_conversation_list_returns_newest_first(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, SpyGraph())
    _signup_login(client, "conversation-order@example.com")
    created = [
        client.post("/conversations", json={"title": "Oldest"}).json(),
        client.post("/conversations", json={"title": "Middle"}).json(),
        client.post("/conversations", json={"title": "Newest"}).json(),
    ]

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        for index, conversation in enumerate(created):
            model = db.get(ConversationModel, conversation["id"])
            assert model is not None
            model.created_at = datetime(2026, 6, 7, 0, index, tzinfo=UTC)
        db.commit()
    finally:
        session_generator.close()

    response = client.get("/conversations")

    assert response.status_code == 200
    assert [conversation["title"] for conversation in response.json()] == [
        "Newest",
        "Middle",
        "Oldest",
    ]


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
    trace = first.json()["agent_trace"]
    assert [step["id"] for step in trace] == [
        "query_cartographer",
        "source_warden",
        "candidate_scouts",
        "evidence_judge",
        "context_curator",
        "assistant_graph",
        "answer_composer",
    ]
    assert trace[0]["title"] == {"en": "Query Cartographer", "ko": "질문 지도화"}
    assert trace[2]["status"] == "skipped"
    assert trace[-1]["evidence"]["citation_count"] == 0


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
    kb_id = _create_knowledge_base(client, "Service Auth KB")
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


def test_debug_logging_exposes_retrieved_context_injected_to_llm(monkeypatch, capsys) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "debug-retrieval@example.com")
    kb_id = _create_knowledge_base(client, "Debug Retrieval KB")
    document = _create_document(
        client,
        json={
            "title": "Debug Retrieval Notes",
            "content": "DebugRetrievalOnly injected chunk boundary note.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Debug logs"}).json()["id"]

    logging.getLogger("my_agents.api.conversations.retrieval_context").setLevel(logging.DEBUG)
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Tell me about my uploaded document.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )

    assert response.status_code == 200
    captured = capsys.readouterr().out
    assert "knowledge context injected to llm" in captured
    assert "knowledge_context_injected_to_llm" in captured
    assert conversation_id in captured
    assert kb_id in captured
    assert document.json()["id"] in captured
    assert "retrieved_chunk_count" in captured
    assert "injected_chunk_count" in captured
    assert "DebugRetrievalOnly" in captured


def test_ambiguous_document_scope_returns_clarification_without_graph(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "clarify-docs@example.com")
    kb_id = _create_knowledge_base(client, "Clarify Docs KB")
    for title in ("Doc A", "Doc B"):
        response = _create_document(
            client,
            json={
                "title": title,
                "content": f"{title} content",
                "knowledge_base_id": kb_id,
            },
        )
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
    assert payload["reply"] == ""
    assert payload["clarification"] == {
        "required": True,
        "kind": "document_scope",
        "reason_code": "ambiguous_document_reference",
        "message_key": "clarification.document_scope.select_source",
        "input_slot": "document_reference",
        "retrieval_route": "clarification_required",
        "document_scope": "unknown",
        "rewritten_query": "이 문서 기준으로 개선점을 알려줘",
    }
    assert graph.calls[-1]["rag_halt_before_response"] is True
    assert graph.calls[-1]["retrieval_route"] == "clarification_required"

    run_id = payload["run_id"]
    detail = client.get(f"/conversations/{conversation_id}/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["reply"] == ""
    assert detail.json()["clarification"]["message_key"] == (
        "clarification.document_scope.select_source"
    )


def test_filename_reference_retrieves_matching_document_metadata(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "filename-lookup@example.com")
    kb_id = _create_knowledge_base(client, "Clinical Protocol KB")
    target = client.post(
        "/documents/upload",
        data={"title": "Clinical Trial Protocol", "knowledge_base_id": kb_id},
        files={
            "file": (
                "NCT06159946_Prot_000.txt",
                b"Clinical protocol discusses eligibility, dosing, and visit schedule.",
                "text/plain",
            )
        },
    )
    distractor = _create_document(
        client,
        json={
            "title": "Other Protocol",
            "content": "Another protocol discusses unrelated monitoring.",
            "knowledge_base_id": kb_id,
        },
    )
    assert target.status_code == 201
    assert distractor.status_code == 201
    assert client.post(f"/documents/{target.json()['id']}/ingest").status_code == 200
    assert client.post(f"/documents/{distractor.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Filename lookup"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "그럼 NCT06159946_Prot_000 이 문서에 대해 설명해줘"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieval_route"] == "retrieval_required"
    assert payload["answer_mode"] == "document_grounded"
    assert payload["clarification"] is None
    assert payload["citations"][0]["document_id"] == target.json()["id"]
    assert payload["citations"][0]["source_filename"] == "NCT06159946_Prot_000.txt"
    assert graph.calls[-1]["retrieved_context"][0]["document_id"] == target.json()["id"]
    assert graph.calls[-1]["retrieved_context"][0]["source"] == "document_metadata"

    events = client.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}/events")
    retrieval_event = next(
        event for event in events.json() if event["event_type"] == "retrieval_completed"
    )
    assert retrieval_event["payload"]["document_metadata_count"] >= 1


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


def test_conversation_transcript_is_owner_only(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    owner = _client(monkeypatch, graph)
    member = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "conv-owner@example.com")
    _signup_login(member, "conv-member@example.com")
    _signup_login(outsider, "conv-outsider@example.com")

    conversation_id = owner.post(
        "/conversations",
        json={"title": "Private Conversation"},
    ).json()["id"]
    run = owner.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Owner-only transcript secret"},
    )
    run_id = run.json()["run_id"]
    assistant_message_id = _assistant_message_id(run_id)

    assert owner.get(f"/conversations/{conversation_id}").status_code == 200
    assert owner.get(f"/conversations/{conversation_id}/messages").status_code == 200
    assert owner.get(f"/conversations/{conversation_id}/runs").status_code == 200

    listed_for_member = member.get("/conversations")
    member_detail = member.get(f"/conversations/{conversation_id}")
    member_messages = member.get(f"/conversations/{conversation_id}/messages")
    member_runs = member.get(f"/conversations/{conversation_id}/runs")
    member_run_detail = member.get(f"/conversations/{conversation_id}/runs/{run_id}")
    member_events = member.get(f"/conversations/{conversation_id}/runs/{run_id}/events")
    member_replay = member.post(
        f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay"
    )
    outsider_detail = outsider.get(f"/conversations/{conversation_id}")

    assert listed_for_member.status_code == 200
    assert conversation_id not in {conversation["id"] for conversation in listed_for_member.json()}
    for response in (
        member_detail,
        member_messages,
        member_runs,
        member_run_detail,
        member_events,
        member_replay,
        outsider_detail,
    ):
        assert response.status_code == 404
        assert "Owner-only transcript secret" not in response.text
        assert "saw 1 messages" not in response.text


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


def test_delete_conversation_removes_transcript_runs_events_and_citations(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "delete-cascade@example.com")
    kb_id = _create_knowledge_base(client, "Delete cascade KB")
    document = _create_document(
        client,
        json={
            "title": "Delete cascade source",
            "content": "DeleteCascadeOnly citation boundary note.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Delete me"}).json()["id"]
    run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Tell me about my uploaded document.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert run.status_code == 200
    assert run.json()["citations"]

    response = client.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 204
    assert client.get(f"/conversations/{conversation_id}").status_code == 404
    assert client.get(f"/conversations/{conversation_id}/messages").status_code == 404
    assert client.get(f"/conversations/{conversation_id}/runs").status_code == 404
    assert (
        client.get(f"/conversations/{conversation_id}/runs/{run.json()['run_id']}").status_code
        == 404
    )
    assert client.delete(f"/conversations/{conversation_id}").status_code == 404
    assert _row_count(ConversationModel) == 0
    assert _row_count(MessageModel) == 0
    assert _row_count(AgentRunModel) == 0
    assert _row_count(AgentEventModel) == 0
    assert _row_count(CitationModel) == 0


def test_delete_conversation_is_owner_only(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    outsider = _client(monkeypatch)
    _signup_login(owner, "delete-owner@example.com")
    _signup_login(outsider, "delete-outsider@example.com")
    conversation_id = owner.post("/conversations", json={"title": "Keep private"}).json()["id"]
    owner.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "private delete transcript"},
    )

    response = outsider.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 404
    assert "private delete transcript" not in response.text
    assert owner.get(f"/conversations/{conversation_id}").status_code == 200
    assert _row_count(ConversationModel) == 1
    assert _row_count(MessageModel) == 1


def test_delete_conversation_requires_auth(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    anonymous = _client(monkeypatch)
    _signup_login(owner, "delete-auth@example.com")
    conversation_id = owner.post("/conversations", json={"title": "Requires auth"}).json()["id"]

    response = anonymous.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 401
    assert owner.get(f"/conversations/{conversation_id}").status_code == 200
    assert _row_count(ConversationModel) == 1


def test_delete_conversation_rejects_active_run(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "delete-active@example.com")
    conversation_id = client.post("/conversations", json={"title": "Active"}).json()["id"]
    _create_running_run(conversation_id=conversation_id, user_id=user_id)

    response = client.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 409
    assert response.json() == {"detail": "conversation run already active"}
    assert client.get(f"/conversations/{conversation_id}").status_code == 200
    assert _row_count(ConversationModel) == 1
    assert _row_count(AgentRunModel) == 1


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


def test_assistant_message_replay_requires_auth_and_ownership(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch, SpyGraph())
    outsider = _client(monkeypatch, SpyGraph())
    _signup_login(owner, "replay-owner@example.com")
    _signup_login(outsider, "replay-outsider@example.com")
    conversation_id = owner.post("/conversations", json={"title": "Replay private"}).json()["id"]
    run = owner.post(f"/conversations/{conversation_id}/runs", json={"message": "Private"})
    assistant_message_id = _assistant_message_id(run.json()["run_id"])

    anonymous = _client(monkeypatch, SpyGraph())
    anonymous_response = anonymous.post(
        f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay"
    )
    outsider_response = outsider.post(
        f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay"
    )

    assert anonymous_response.status_code == 401
    assert outsider_response.status_code == 404
    assert "Private" not in outsider_response.text


def test_assistant_message_replay_rejects_non_assistant_message(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, SpyGraph())
    _signup_login(client, "replay-user-message@example.com")
    conversation_id = client.post("/conversations", json={"title": "Replay user"}).json()["id"]
    user_message = client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "I am not assistant output"},
    )

    response = client.post(
        f"/conversations/{conversation_id}/messages/{user_message.json()['id']}/replay"
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "message is not an assistant message"}


def test_assistant_message_replay_hides_missing_or_foreign_message(monkeypatch) -> None:  # noqa: ANN001
    owner = _client(monkeypatch, SpyGraph())
    other = _client(monkeypatch, SpyGraph())
    _signup_login(owner, "replay-notfound-owner@example.com")
    _signup_login(other, "replay-notfound-other@example.com")
    owner_conversation_id = owner.post("/conversations", json={"title": "Replay owner"}).json()[
        "id"
    ]
    other_conversation_id = other.post("/conversations", json={"title": "Replay other"}).json()[
        "id"
    ]
    other_run = other.post(f"/conversations/{other_conversation_id}/runs", json={"message": "Hi"})
    foreign_message_id = _assistant_message_id(other_run.json()["run_id"])

    missing = owner.post(f"/conversations/{owner_conversation_id}/messages/not-a-message/replay")
    foreign = owner.post(
        f"/conversations/{owner_conversation_id}/messages/{foreign_message_id}/replay"
    )

    assert missing.status_code == 404
    assert missing.json() == {"detail": "message not found"}
    assert foreign.status_code == 404
    assert foreign.json() == {"detail": "message not found"}


def test_assistant_message_replay_prunes_later_transcript_and_regenerates(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "replay-success@example.com")
    conversation_id = client.post("/conversations", json={"title": "Replay success"}).json()["id"]
    first = client.post(f"/conversations/{conversation_id}/runs", json={"message": "First"})
    second = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Second"})
    first_run_id = first.json()["run_id"]
    second_run_id = second.json()["run_id"]
    first_assistant_id = _assistant_message_id(first_run_id)

    response = client.post(f"/conversations/{conversation_id}/messages/{first_assistant_id}/replay")

    assert response.status_code == 200
    replay_payload = response.json()
    assert replay_payload["run_id"] not in {first_run_id, second_run_id}
    assert replay_payload["reply"] == "saw 1 messages"
    assert [message.content for message in graph.calls[-1]["messages"]] == ["First"]

    transcript = client.get(f"/conversations/{conversation_id}/messages")
    runs = client.get(f"/conversations/{conversation_id}/runs")

    assert [(message["role"], message["content"]) for message in transcript.json()] == [
        ("user", "First"),
        ("assistant", "saw 1 messages"),
    ]
    assert [run["run_id"] for run in runs.json()] == [replay_payload["run_id"]]
    assert _row_count(AgentEventModel) == 5
    assert _row_count(CitationModel) == 0


def test_streaming_assistant_message_replay_emits_deltas_and_prunes_after_success(
    monkeypatch,
) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "replay-stream-success@example.com")
    conversation_id = client.post("/conversations", json={"title": "Replay stream success"}).json()[
        "id"
    ]
    first = client.post(f"/conversations/{conversation_id}/runs", json={"message": "First"})
    second = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Second"})
    first_run_id = first.json()["run_id"]
    second_run_id = second.json()["run_id"]
    first_assistant_id = _assistant_message_id(first_run_id)
    streaming_graph = StreamingSpyGraph()
    client.app.dependency_overrides[get_graph_runner] = lambda: streaming_graph

    assert not hasattr(retrieval_context_module, "prepare_retrieval_context")
    assert not hasattr(replay_endpoint, "prepare_retrieval_context")

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages/{first_assistant_id}/replay/stream",
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
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
    assert [event["data"]["sequence"] for event in delta_events] == [1, 2]
    assert "".join(event["data"]["delta"] for event in delta_events) == "streamed answer"
    assert completed["run_id"] not in {first_run_id, second_run_id}
    assert completed["reply"] == "streamed answer"
    assert [message.content for message in streaming_graph.calls[-1]["messages"]] == ["First"]

    transcript = client.get(f"/conversations/{conversation_id}/messages")
    runs = client.get(f"/conversations/{conversation_id}/runs")

    assert [(message["role"], message["content"]) for message in transcript.json()] == [
        ("user", "First"),
        ("assistant", "streamed answer"),
    ]
    assert [run["run_id"] for run in runs.json()] == [completed["run_id"]]


def test_assistant_message_replay_failure_preserves_existing_transcript(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, SpyGraph())
    _signup_login(client, "replay-failure-preserve@example.com")
    conversation_id = client.post("/conversations", json={"title": "Replay failure"}).json()["id"]
    first = client.post(f"/conversations/{conversation_id}/runs", json={"message": "First"})
    second = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Second"})
    first_assistant_id = _assistant_message_id(first.json()["run_id"])

    client.app.dependency_overrides[get_graph_runner] = lambda: FailingGraph()

    response = client.post(f"/conversations/{conversation_id}/messages/{first_assistant_id}/replay")

    assert response.status_code == 502
    assert response.json() == {"detail": "conversation run failed"}
    transcript = client.get(f"/conversations/{conversation_id}/messages")
    assert [(message["role"], message["content"]) for message in transcript.json()] == [
        ("user", "First"),
        ("assistant", "saw 1 messages"),
        ("user", "Second"),
        ("assistant", "saw 3 messages"),
    ]
    runs = client.get(f"/conversations/{conversation_id}/runs").json()
    assert {run["run_id"] for run in runs}.issuperset(
        {first.json()["run_id"], second.json()["run_id"]}
    )
    assert runs[0]["status"] == "failed"
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        failed_run = db.get(AgentRunModel, runs[0]["run_id"])
        assert failed_run is not None
        assert failed_run.assistant_message_id is None
    finally:
        session_generator.close()


def test_streaming_assistant_message_replay_failure_preserves_existing_transcript(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch, SpyGraph())
    _signup_login(client, "replay-stream-failure-preserve@example.com")
    conversation_id = client.post("/conversations", json={"title": "Replay stream failure"}).json()[
        "id"
    ]
    first = client.post(f"/conversations/{conversation_id}/runs", json={"message": "First"})
    second = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Second"})
    first_assistant_id = _assistant_message_id(first.json()["run_id"])

    client.app.dependency_overrides[get_graph_runner] = lambda: FailingGraph()

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages/{first_assistant_id}/replay/stream",
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    assert [event["event"] for event in events][-2:] == ["run_failed", "run_error"]
    assert events[-1]["data"]["status_code"] == 502
    transcript = client.get(f"/conversations/{conversation_id}/messages")
    assert [(message["role"], message["content"]) for message in transcript.json()] == [
        ("user", "First"),
        ("assistant", "saw 1 messages"),
        ("user", "Second"),
        ("assistant", "saw 3 messages"),
    ]
    runs = client.get(f"/conversations/{conversation_id}/runs").json()
    assert {run["run_id"] for run in runs}.issuperset(
        {first.json()["run_id"], second.json()["run_id"]}
    )
    assert runs[0]["status"] == "failed"


def test_assistant_message_replay_warns_when_original_sources_are_deleted(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "replay-missing-source@example.com")
    kb_id = _create_knowledge_base(client, "Replay deleted source KB")
    document = _create_document(
        client,
        json={
            "title": "Replay deleted source",
            "content": "ReplayDeletedOnly original source boundary note.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    document_id = document.json()["id"]
    assert client.post(f"/documents/{document_id}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Replay deleted doc"}).json()[
        "id"
    ]
    original = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Tell me about my uploaded document.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert original.status_code == 200
    assert original.json()["citations"]
    assert original.json()["warnings"] == []
    assert _run_source_snapshot(original.json()["run_id"]) is not None
    assert len(graph.calls) == 1
    assistant_message_id = _assistant_message_id(original.json()["run_id"])

    assert client.delete(f"/documents/{document_id}").status_code == 204
    replay = client.post(f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay")

    assert replay.status_code == 200
    payload = replay.json()
    assert payload["warnings"] == [
        {
            "code": "regeneration_sources_unavailable",
            "message": (
                "Some sources used in the original answer are no longer available. "
                "This regeneration used currently available knowledge only."
            ),
            "missing_document_ids": [document_id],
            "missing_source_filenames": [],
        }
    ]
    assert all(citation["document_id"] != document_id for citation in payload["citations"])
    assert "enough relevant authorized document evidence" in payload["reply"]
    assert len(graph.calls) == 2
    assert graph.calls[-1]["rag_halt_before_response"] is True
    assert graph.calls[-1]["retrieval_route"] == "retrieval_required"
    assert graph.calls[-1]["retrieved_context"] == []


def test_assistant_message_replay_preserves_original_kb_selection(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "replay-kb@example.com")
    kb_a = _create_knowledge_base(client, "Replay Selected A")
    kb_b = _create_knowledge_base(client, "Replay Selected B")
    doc_a = _create_document(
        client,
        json={
            "title": "Replay selected source",
            "content": "ReplayAlphaOnly selected replay boundary note.",
            "knowledge_base_id": kb_a,
        },
    )
    doc_b = _create_document(
        client,
        json={
            "title": "Replay unselected source",
            "content": "ReplayBetaOnly unselected replay boundary note.",
            "knowledge_base_id": kb_b,
        },
    )
    assert client.post(f"/documents/{doc_a.json()['id']}/ingest").status_code == 200
    assert client.post(f"/documents/{doc_b.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Replay KB"}).json()["id"]
    original = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Tell me about my uploaded document.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_a]},
        },
    )
    assistant_message_id = _assistant_message_id(original.json()["run_id"])

    replay = client.post(
        f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay",
        json={"knowledge_base_selection": {"mode": "all", "knowledge_base_ids": []}},
    )

    assert replay.status_code == 200
    payload = replay.json()
    assert payload["knowledge_base_selection"] == {
        "mode": "selected",
        "knowledge_base_ids": [kb_a],
    }
    assert payload["resolved_knowledge_base_count"] == 1
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {kb_a}
    assert any("ReplayAlphaOnly" in citation["snippet"] for citation in payload["citations"])
    assert "ReplayBetaOnly" not in payload["reply"]
    assert {context["document_id"] for context in graph.calls[-1]["retrieved_context"]} == {
        doc_a.json()["id"]
    }


def test_assistant_message_replay_uses_request_kb_selection_when_original_run_missing(
    monkeypatch,
) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "replay-kb-fallback@example.com")
    kb_id = _create_knowledge_base(client, "Replay explicit fallback")
    document = _create_document(
        client,
        json={
            "title": "Replay fallback source",
            "content": "ReplayFallbackOnly explicit fallback boundary note.",
            "knowledge_base_id": kb_id,
        },
    )
    assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Replay fallback"}).json()["id"]
    user_message = client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "Tell me about my uploaded document."},
    )
    assert user_message.status_code == 201
    assistant_message_id = _create_orphan_assistant_message(
        conversation_id=conversation_id,
        content="Old orphan answer",
    )

    replay = client.post(
        f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay",
        json={"knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]}},
    )

    assert replay.status_code == 200
    payload = replay.json()
    assert payload["knowledge_base_selection"] == {
        "mode": "selected",
        "knowledge_base_ids": [kb_id],
    }
    assert payload["citations"]
    assert any("ReplayFallbackOnly" in citation["snippet"] for citation in payload["citations"])


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


def test_streaming_required_retrieval_without_evidence_skips_graph_safely(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "stream-insufficient@example.com")
    conversation_id = client.post("/conversations", json={"title": "Missing evidence"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "Summarize my uploaded document"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    assert [event["event"] for event in events] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "answer_composed",
        "run_completed",
    ]
    retrieval_completed = events[2]["data"]
    answer_composed = events[3]["data"]
    completed = events[4]["data"]

    assert retrieval_completed["retrieval_route"] == "retrieval_required"
    assert retrieval_completed["retrieval_attempt_count"] == 2
    assert retrieval_completed["retrieval_retry_count"] == 1
    assert retrieval_completed["insufficient_evidence"] is True
    assert answer_composed["insufficient_evidence"] is True
    assert completed["answer_mode"] == "general_knowledge"
    assert completed["citations"] == []
    assert "enough relevant authorized document evidence" in completed["reply"]
    assert graph.calls[-1]["rag_halt_before_response"] is True
    assert graph.calls[-1]["retrieval_route"] == "retrieval_required"

    persisted = client.get(f"/conversations/{conversation_id}/runs/{completed['run_id']}/events")
    assert [event["event_type"] for event in persisted.json()] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "answer_composed",
    ]


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
    started = events[0]["data"]
    assert started["conversation_id"] == conversation_id
    assert started["knowledge_base_selection"] == {"mode": "all", "knowledge_base_ids": []}
    assert started["resolved_knowledge_base_count"] >= 0

    completed = events[-1]["data"]
    assert completed["conversation_id"] == conversation_id
    assert completed["reply"] == "saw 1 messages"
    assert completed["handled_by"] == "personal_assistant_graph"
    assert completed["route"]["label"] == "general_assistant"
    assert graph.calls[0]["conversation_id"] == conversation_id
    assert [step["id"] for step in completed["agent_trace"]][-2:] == [
        "assistant_graph",
        "answer_composer",
    ]
    assert completed["agent_trace"][0]["title"]["ko"] == "질문 지도화"

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
    persisted_payloads = {event["event_type"]: event["payload"] for event in run_events.json()}
    assert persisted_payloads["retrieval_completed"]["agent_trace"][0]["id"] == (
        "query_cartographer"
    )
    assert persisted_payloads["graph_invoked"]["agent_trace"][0]["title"] == {
        "en": "Assistant Graph",
        "ko": "어시스턴트 그래프",
    }


def test_streaming_ambiguous_document_scope_emits_human_clarification_state(
    monkeypatch,
) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "stream-clarify-docs@example.com")
    kb_id = _create_knowledge_base(client, "Stream Clarify Docs KB")
    for title in ("Stream Doc A", "Stream Doc B"):
        response = _create_document(
            client,
            json={
                "title": title,
                "content": f"{title} content",
                "knowledge_base_id": kb_id,
            },
        )
        assert response.status_code == 201
    conversation_id = client.post("/conversations", json={"title": "Stream clarify"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={"message": "이 문서 기준으로 개선점을 알려줘"},
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    event_names = [event["event"] for event in events]
    events_by_name = {event["event"]: event["data"] for event in events}
    answer_composed = events_by_name["answer_composed"]
    completed = events_by_name["run_completed"]

    assert event_names == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "answer_composed",
        "run_completed",
    ]
    assert "graph_invoked" not in event_names
    assert graph.calls[-1]["rag_halt_before_response"] is True
    assert graph.calls[-1]["retrieval_route"] == "clarification_required"
    assert answer_composed["reply_length"] == 0
    assert answer_composed["clarification_required"] is True
    assert answer_composed["clarification"]["message_key"] == (
        "clarification.document_scope.select_source"
    )
    assert completed["reply"] == ""
    assert completed["clarification"]["input_slot"] == "document_reference"
    assert completed["agent_trace"][-1]["id"] == "answer_composer"
    assert completed["agent_trace"][-1]["status"] == "waiting"


def test_streaming_selected_kb_run_uses_fallback_only_in_selected_scope(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "stream-selected-kb@example.com")
    kb_a = _create_knowledge_base(client, "Stream Selected A")
    kb_b = _create_knowledge_base(client, "Stream Selected B")
    doc_a = _create_document(
        client,
        json={
            "title": "Selected Stream Notes",
            "content": "AlphaStreamOnly selected fallback boundary note.",
            "knowledge_base_id": kb_a,
        },
    )
    doc_b = _create_document(
        client,
        json={
            "title": "Unselected Stream Notes",
            "content": "BetaStreamOnly unselected fallback boundary note.",
            "knowledge_base_id": kb_b,
        },
    )
    assert doc_a.status_code == 201
    assert doc_b.status_code == 201
    assert client.post(f"/documents/{doc_a.json()['id']}/ingest").status_code == 200
    assert client.post(f"/documents/{doc_b.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Selected stream"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={
            "message": "Tell me about my uploaded document.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_a]},
        },
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    events_by_name = {event["event"]: event["data"] for event in events}
    selected_payload = {"mode": "selected", "knowledge_base_ids": [kb_a]}
    retrieval_completed = events_by_name["retrieval_completed"]
    graph_invoked = events_by_name["graph_invoked"]
    answer_composed = events_by_name["answer_composed"]
    completed = events_by_name["run_completed"]

    assert retrieval_completed["knowledge_base_selection"] == selected_payload
    assert retrieval_completed["resolved_knowledge_base_count"] == 1
    assert retrieval_completed["fallback_count"] == 1
    assert retrieval_completed["authorized_context_count"] == 1
    assert graph_invoked["knowledge_base_selection"] == selected_payload
    assert graph_invoked["retrieved_chunk_count"] == 1
    assert answer_composed["knowledge_base_selection"] == selected_payload
    assert answer_composed["citation_count"] == 1
    assert completed["knowledge_base_selection"] == selected_payload
    assert completed["resolved_knowledge_base_count"] == 1
    assert {citation["knowledge_base_id"] for citation in completed["citations"]} == {kb_a}
    assert any("AlphaStreamOnly" in citation["snippet"] for citation in completed["citations"])
    assert "BetaStreamOnly" not in completed["reply"]
    assert {context["document_id"] for context in graph.calls[-1]["retrieved_context"]} == {
        doc_a.json()["id"]
    }
    assert graph.calls[-1]["retrieved_context"][0]["source"] == "document_fallback"

    detail = client.get(f"/conversations/{conversation_id}/runs/{completed['run_id']}")
    run_events = client.get(f"/conversations/{conversation_id}/runs/{completed['run_id']}/events")

    assert detail.status_code == 200
    assert detail.json()["knowledge_base_selection"] == selected_payload
    assert {citation["knowledge_base_id"] for citation in detail.json()["citations"]} == {kb_a}
    persisted_payloads = {event["event_type"]: event["payload"] for event in run_events.json()}
    assert persisted_payloads["retrieval_completed"]["knowledge_base_selection"] == selected_payload
    assert persisted_payloads["retrieval_completed"]["fallback_count"] == 1
    assert persisted_payloads["graph_invoked"]["knowledge_base_selection"] == selected_payload
    assert persisted_payloads["answer_composed"]["knowledge_base_selection"] == selected_payload


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


def test_active_run_stale_threshold_is_configurable(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_ACTIVE_RUN_STALE_AFTER_SECONDS", "5")
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    user_id = _signup_login(client, "stale-run-threshold@example.com")
    recent_conversation_id = client.post(
        "/conversations", json={"title": "Recent active threshold"}
    ).json()["id"]
    recent_run_id = _create_running_run(
        conversation_id=recent_conversation_id,
        user_id=user_id,
        created_at=datetime.now(UTC) - timedelta(seconds=4),
    )

    blocked_response = client.post(
        f"/conversations/{recent_conversation_id}/runs", json={"message": "Too soon"}
    )

    assert blocked_response.status_code == 409
    runs_before_cutoff = client.get(f"/conversations/{recent_conversation_id}/runs").json()
    assert runs_before_cutoff[0]["run_id"] == recent_run_id
    assert runs_before_cutoff[0]["status"] == "running"

    stale_conversation_id = client.post(
        "/conversations", json={"title": "Stale active threshold"}
    ).json()["id"]
    stale_run_id = _create_running_run(
        conversation_id=stale_conversation_id,
        user_id=user_id,
        created_at=datetime.now(UTC) - timedelta(seconds=6),
    )

    recovered_response = client.post(
        f"/conversations/{stale_conversation_id}/runs", json={"message": "After stale"}
    )

    assert recovered_response.status_code == 200
    runs = client.get(f"/conversations/{stale_conversation_id}/runs").json()
    runs_by_id = {run["run_id"]: run for run in runs}
    assert runs_by_id[stale_run_id]["status"] == "failed"
    assert runs_by_id[recovered_response.json()["run_id"]]["status"] == "completed"


def test_stale_active_run_is_terminalized_before_new_run(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_ACTIVE_RUN_STALE_AFTER_SECONDS", "120")
    graph = SpyGraph()
    client = _client(monkeypatch, graph)
    user_id = _signup_login(client, "stale-run@example.com")
    conversation_id = client.post("/conversations", json={"title": "Stale run"}).json()["id"]
    stale_run_id = _create_running_run(
        conversation_id=conversation_id,
        user_id=user_id,
        created_at=datetime.now(UTC) - timedelta(minutes=30),
    )

    response = client.post(
        f"/conversations/{conversation_id}/runs", json={"message": "After stale"}
    )

    assert response.status_code == 200
    runs = client.get(f"/conversations/{conversation_id}/runs").json()
    runs_by_id = {run["run_id"]: run for run in runs}
    assert runs_by_id[stale_run_id]["status"] == "failed"
    assert response.json()["run_id"] in runs_by_id
    assert runs_by_id[response.json()["run_id"]]["status"] == "completed"

    stale_events = client.get(f"/conversations/{conversation_id}/runs/{stale_run_id}/events")
    assert stale_events.status_code == 200
    assert stale_events.json()[-1]["event_type"] == "run_failed"
    assert stale_events.json()[-1]["payload"] == {
        "safe_error_type": "StaleActiveRun",
        "safe_reason": "active run exceeded stale timeout",
        "stale_active_run_cleanup": True,
    }


def test_list_runs_terminalizes_stale_active_run(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, SpyGraph())
    user_id = _signup_login(client, "stale-run-list@example.com")
    conversation_id = client.post("/conversations", json={"title": "Stale run list"}).json()["id"]
    stale_run_id = _create_running_run(
        conversation_id=conversation_id,
        user_id=user_id,
        created_at=datetime.now(UTC) - timedelta(minutes=30),
    )

    runs = client.get(f"/conversations/{conversation_id}/runs")

    assert runs.status_code == 200
    payload = runs.json()
    assert len(payload) == 1
    assert payload[0]["run_id"] == stale_run_id
    assert payload[0]["conversation_id"] == conversation_id
    assert payload[0]["status"] == "failed"
    assert payload[0]["route_label"] is None

    response = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Recovered"})
    assert response.status_code == 200

    stale_events = client.get(f"/conversations/{conversation_id}/runs/{stale_run_id}/events")
    assert stale_events.status_code == 200
    assert stale_events.json()[-1]["event_type"] == "run_failed"
    assert stale_events.json()[-1]["payload"] == {
        "safe_error_type": "StaleActiveRun",
        "safe_reason": "active run exceeded stale timeout",
        "stale_active_run_cleanup": True,
    }


def test_list_runs_terminalizes_stale_cancelling_run(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, SpyGraph())
    user_id = _signup_login(client, "stale-cancelling-run-list@example.com")
    conversation_id = client.post(
        "/conversations", json={"title": "Stale cancelling run list"}
    ).json()["id"]
    stale_run_id = _create_running_run(
        conversation_id=conversation_id,
        user_id=user_id,
        status=RunStatus.CANCELLING.value,
        created_at=datetime.now(UTC) - timedelta(minutes=30),
    )

    runs = client.get(f"/conversations/{conversation_id}/runs")

    assert runs.status_code == 200
    payload = runs.json()
    assert len(payload) == 1
    assert payload[0]["run_id"] == stale_run_id
    assert payload[0]["status"] == "cancelled"

    stale_events = client.get(f"/conversations/{conversation_id}/runs/{stale_run_id}/events")
    assert stale_events.status_code == 200
    assert stale_events.json()[-1]["event_type"] == "run_cancelled"
    assert stale_events.json()[-1]["payload"] == {
        "run_id": stale_run_id,
        "conversation_id": conversation_id,
        "status": "cancelled",
        "partial_reply_persisted": False,
        "stale_active_run_cleanup": True,
    }


def test_sync_post_start_failure_terminalizes_run(monkeypatch) -> None:  # noqa: ANN001
    graph = SpyGraph()
    client = _client(monkeypatch, graph, raise_server_exceptions=False)
    _signup_login(client, "post-start-failure@example.com")
    conversation_id = client.post("/conversations", json={"title": "Post-start failure"}).json()[
        "id"
    ]

    from my_agents.agents.rag_agent.retrieval import SqlAlchemyRagAgentRuntime

    original_retrieve_context = SqlAlchemyRagAgentRuntime.retrieve_context

    def fail_retrieval_context(self, **kwargs: Any):  # noqa: ANN001, ARG001
        raise RuntimeError("retrieval failed after run start")

    monkeypatch.setattr(SqlAlchemyRagAgentRuntime, "retrieve_context", fail_retrieval_context)

    failed = client.post(
        f"/conversations/{conversation_id}/runs", json={"message": "Fail after start"}
    )

    assert failed.status_code == 502
    assert failed.json() == {"detail": "conversation run failed"}
    failed_run = client.get(f"/conversations/{conversation_id}/runs").json()[0]
    assert failed_run["status"] == "failed"
    events = client.get(f"/conversations/{conversation_id}/runs/{failed_run['run_id']}/events")
    assert [event["event_type"] for event in events.json()] == [
        "run_started",
        "user_message_stored",
        "run_failed",
    ]
    assert events.json()[-1]["payload"] == {"safe_error_type": "RuntimeError"}

    monkeypatch.setattr(SqlAlchemyRagAgentRuntime, "retrieve_context", original_retrieve_context)
    recovered = client.post(f"/conversations/{conversation_id}/runs", json={"message": "Recovered"})
    assert recovered.status_code == 200


def test_stream_generator_close_after_start_cancels_without_partial_assistant(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch, StreamingSpyGraph())
    user_id = _signup_login(client, "stream-close@example.com")
    conversation_id = client.post("/conversations", json={"title": "Stream close"}).json()["id"]
    session_generator = get_database_session()
    db = next(session_generator)
    stream = conversation_run_events(
        db=db,
        conversation_id=conversation_id,
        request=ConversationRunRequest(message="Close after start"),
        user_id=user_id,
        selection_context=KnowledgeBaseSelectionContext(
            mode="all",
            knowledge_base_ids=(),
            resolved_count=0,
        ),
        graph_runner=StreamingSpyGraph(),
    )

    try:
        first_event = next(stream)
        run_id = _parse_sse(first_event)[0]["data"]["run_id"]
        stream.close()
    finally:
        session_generator.close()

    transcript = client.get(f"/conversations/{conversation_id}/messages").json()
    runs = client.get(f"/conversations/{conversation_id}/runs").json()
    run_events = client.get(f"/conversations/{conversation_id}/runs/{run_id}/events").json()

    assert [(message["role"], message["content"]) for message in transcript] == [
        ("user", "Close after start"),
    ]
    assert runs[0]["status"] == "cancelled"
    assert [event["event_type"] for event in run_events] == [
        "run_started",
        "user_message_stored",
        "run_cancelled",
    ]


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
    client = _client(monkeypatch, SpyGraph())

    response = client.post("/assistant/chat", json={"message": "Hello", "history": []})

    assert response.status_code == 200
    payload = response.json()
    assert payload["handled_by"] == "personal_assistant_graph"
    assert "run_id" not in payload
    assert "citations" not in payload


def _create_running_run(
    *,
    conversation_id: str,
    user_id: str,
    status: str = RunStatus.RUNNING.value,
    created_at: datetime | None = None,
) -> str:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = AgentRunModel(
            conversation_id=conversation_id,
            user_id=user_id,
            status=status,
        )
        if created_at is not None:
            run.created_at = created_at
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        session_generator.close()


def _assistant_message_id(run_id: str) -> str:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = db.get(AgentRunModel, run_id)
        assert run is not None
        assert run.assistant_message_id is not None
        return run.assistant_message_id
    finally:
        session_generator.close()


def _create_orphan_assistant_message(*, conversation_id: str, content: str) -> str:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        message = MessageModel(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message.id
    finally:
        session_generator.close()


def _run_source_snapshot(run_id: str) -> str | None:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = db.get(AgentRunModel, run_id)
        assert run is not None
        return run.retrieval_source_snapshot_json
    finally:
        session_generator.close()


def _row_count(model: type) -> int:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        return db.scalar(select(func.count()).select_from(model)) or 0
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


def test_conversation_run_injects_enabled_user_memory_and_conflicts(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "memory-context@example.com")
    assert client.patch("/memories/settings", json={"enabled": True}).status_code == 200
    created_memory = client.post(
        "/memories",
        json={"content": "User prefers concise answers", "category": "stable_preference"},
    )
    assert created_memory.status_code == 201
    conversation_id = client.post("/conversations", json={"title": "Memory Context"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Actually I no longer prefer concise answers"},
    )

    assert response.status_code == 200
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = db.scalar(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id == conversation_id)
            .order_by(AgentRunModel.created_at.desc())
        )
        assert run is not None
        assert run.memory_source_snapshot_json is not None
        memory_snapshot = json.loads(run.memory_source_snapshot_json)
        assert memory_snapshot["memory_count"] == 1
        assert memory_snapshot["conflict_count"] == 1
        assert memory_snapshot["memories"][0]["id"] == created_memory.json()["id"]
        assert "User prefers concise answers" not in run.memory_source_snapshot_json
        graph_event = db.scalar(
            select(AgentEventModel)
            .where(
                AgentEventModel.run_id == run.id,
                AgentEventModel.event_type == AgentEventType.GRAPH_INVOKED.value,
            )
            .order_by(AgentEventModel.sequence.desc())
        )
        assert graph_event is not None
        event_payload = json.loads(graph_event.payload_json)
        assert event_payload["memory_count"] == 1
        assert event_payload["memory_categories"] == ["stable_preference"]
        assert event_payload["memory_provenance_types"] == ["explicit_user"]
        assert "memory_source_snapshot" not in event_payload
        assert created_memory.json()["id"] not in graph_event.payload_json
        assert "User prefers concise answers" not in graph_event.payload_json
    finally:
        session_generator.close()


def test_failed_conversation_run_preserves_internal_memory_audit_without_public_ids(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(
        monkeypatch,
        MemoryUpdateThenFailingGraph(),
        raise_server_exceptions=False,
    )
    _signup_login(client, "memory-failure-audit@example.com")
    conversation_id = client.post("/conversations", json={"title": "Memory Failure"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Use my memory, then fail"},
    )

    assert response.status_code == 502
    failed_run = client.get(f"/conversations/{conversation_id}/runs").json()[0]
    assert failed_run["status"] == "failed"

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = db.get(AgentRunModel, failed_run["run_id"])
        assert run is not None
        assert run.memory_source_snapshot_json is not None
        internal_snapshot = json.loads(run.memory_source_snapshot_json)
        assert internal_snapshot["memory_count"] == 1
        assert internal_snapshot["memories"][0]["id"] == "memory-internal-1"
        assert internal_snapshot["memories"][0]["source_message_id"] == "message-internal-1"

        graph_event = db.scalar(
            select(AgentEventModel)
            .where(
                AgentEventModel.run_id == run.id,
                AgentEventModel.event_type == AgentEventType.GRAPH_INVOKED.value,
            )
            .order_by(AgentEventModel.sequence.desc())
        )
        assert graph_event is not None
        event_payload = json.loads(graph_event.payload_json)
        assert event_payload["memory_count"] == 1
        assert event_payload["memory_categories"] == ["stable_preference"]
        assert event_payload["memory_provenance_types"] == ["manual"]
        assert "memory_source_snapshot" not in event_payload
        public_payload = json.dumps(event_payload)
        assert "memory-internal-1" not in public_payload
        assert "message-internal-1" not in public_payload
        assert "run-internal-1" not in public_payload
        assert "document-internal-1" not in public_payload
        assert "User prefers concise answers" not in public_payload
    finally:
        session_generator.close()


def test_conversation_run_excludes_disabled_user_memory(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "memory-disabled-context@example.com")
    assert client.patch("/memories/settings", json={"enabled": True}).status_code == 200
    assert (
        client.post(
            "/memories",
            json={"content": "User prefers concise answers", "category": "stable_preference"},
        ).status_code
        == 201
    )
    assert client.patch("/memories/settings", json={"enabled": False}).status_code == 200
    conversation_id = client.post("/conversations", json={"title": "No Memory"}).json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Plan my next backend task"},
    )

    assert response.status_code == 200
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        run = db.scalar(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id == conversation_id)
            .order_by(AgentRunModel.created_at.desc())
        )
        assert run is not None
        assert run.memory_source_snapshot_json is None
        graph_event = db.scalar(
            select(AgentEventModel)
            .where(
                AgentEventModel.run_id == run.id,
                AgentEventModel.event_type == AgentEventType.GRAPH_INVOKED.value,
            )
            .order_by(AgentEventModel.sequence.desc())
        )
        assert graph_event is not None
        event_payload = json.loads(graph_event.payload_json)
        assert "memory_count" not in event_payload
        assert "memory_source_snapshot" not in event_payload
    finally:
        session_generator.close()
