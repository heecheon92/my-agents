"""Full-document retrieval tests for intent, authorization, coverage, and persistence."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from sqlalchemy import select

from my_agents.agents.general_assistant.graph import build_graph
from my_agents.agents.rag_agent import SqlAlchemyRagAgentRuntime
from my_agents.api.assistant import get_graph_runner
from my_agents.api.conversations.graph_streaming import fallback_answer_deltas
from my_agents.api.conversations.retrieval_context import (
    document_coverage_from_graph_state,
)
from my_agents.conversations.models import AgentEventModel
from my_agents.conversations.schemas import DocumentCoverageResponse
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import (
    CitationModel,
    DocumentChunkModel,
    DocumentModel,
    KnowledgeBaseModel,
    KnowledgeBasePurpose,
    KnowledgeBaseScope,
)
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.knowledge.routing import is_comprehensive_document_request
from my_agents.persistence.database import get_database_session
from my_agents.persistence.langgraph import checkpoint_serializer

from .conftest import graph_state
from .test_conversations_api import (
    _client,
    _create_document,
    _create_knowledge_base,
    _parse_sse,
    _signup_login,
)
from .test_graph import FakeRetrievalSourceDecider


def _make_document_stale_after_prepared_read(
    monkeypatch: pytest.MonkeyPatch,
    *,
    document_id: str,
) -> list[int]:
    """Change the document after preparation so the response-node re-read downgrades."""
    original_read = SqlAlchemyRagAgentRuntime.read_full_document_range
    calls: list[int] = []

    def read_then_change_document(self, **kwargs):  # noqa: ANN001, ANN003, ANN202
        result = original_read(self, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            document = self.db.get(DocumentModel, document_id)
            assert document is not None
            document.content = f"{document.content}\n\nChanged after coverage preparation."
            self.db.commit()
        return result

    monkeypatch.setattr(
        SqlAlchemyRagAgentRuntime,
        "read_full_document_range",
        read_then_change_document,
    )
    return calls


def _coverage_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": "complete",
        "document_id": "document-1",
        "title": "Coverage Source",
        "source_filename": None,
        "start_offset": 0,
        "end_offset": 10,
        "total_chars": 10,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "message",
    [
        "Review the entire document and identify every requirement.",
        "Analyze the whole file from beginning to end.",
        "문서 전체를 빠짐없이 검토해줘.",
        "문서의 모든 요구사항을 추출해줘.",
        "Markdown Langgraph - Pydantic Annotated Literal.md 문서를 모두 읽고 내용을 요약해줘",
    ],
)
def test_explicit_comprehensive_document_intent(message: str) -> None:
    assert is_comprehensive_document_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "What does full document retrieval mean?",
        "Summarize this document.",
        "What is a complete document?",
        "문서 전체라는 말은 무슨 뜻이야?",
    ],
)
def test_non_task_or_ordinary_document_requests_do_not_trigger_full_read(message: str) -> None:
    assert is_comprehensive_document_request(message) is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"start_offset": 6, "end_offset": 5},
            "start_offset must not exceed end_offset",
        ),
        (
            {"end_offset": 11},
            "end_offset must not exceed total_chars",
        ),
        (
            {"start_offset": 1},
            "complete document coverage must span",
        ),
        (
            {"end_offset": 9},
            "complete document coverage must span",
        ),
    ],
)
def test_document_coverage_rejects_inconsistent_ranges(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        DocumentCoverageResponse.model_validate(_coverage_payload(**overrides))


def test_partial_document_coverage_mode_remains_authoritative_at_total_chars() -> None:
    coverage = DocumentCoverageResponse.model_validate(_coverage_payload(mode="partial"))

    assert coverage.mode == "partial"
    assert coverage.end_offset == coverage.total_chars


def test_empty_document_coverage_sentinel_means_no_public_coverage() -> None:
    assert document_coverage_from_graph_state({"document_coverage": {}}) is None


def test_authorized_full_document_read_uses_half_open_ranges(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "full-range@example.com")
    kb_id = _create_knowledge_base(client, "Full range KB")
    content = "Alpha requirement.\n\n" + ("Boundary evidence sentence. " * 30)
    document = _create_document(
        client,
        json={"title": "Range Source", "content": content, "knowledge_base_id": kb_id},
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = RetrievalService(db)
        resolution = service.resolve_full_document_target(
            user_id=user_id,
            query="Review the entire document",
        )
        assert resolution.target is not None
        assert resolution.target.document_id == document["id"]

        complete = service.read_full_document_range(
            user_id=user_id,
            document_id=document["id"],
            full_document_max_chars=len(content),
            range_chars=100,
        )
        assert complete is not None
        assert complete.complete is True
        assert complete.content == content
        assert complete.start_offset == 0
        assert complete.end_offset == len(content)
        assert complete.next_cursor is None
        assert complete.retrieved_chunks

        partial = service.read_full_document_range(
            user_id=user_id,
            document_id=document["id"],
            full_document_max_chars=100,
            range_chars=80,
        )
        assert partial is not None
        assert partial.complete is False
        assert partial.content == content[:80]
        assert partial.start_offset == 0
        assert partial.end_offset == 80
        assert partial.next_cursor == "80"
        assert all(
            item.chunk.start_offset < 80 and item.chunk.end_offset > 0
            for item in partial.retrieved_chunks
        )

        continued = service.read_full_document_range(
            user_id=user_id,
            document_id=document["id"],
            cursor=partial.next_cursor,
            full_document_max_chars=100,
            range_chars=80,
        )
        assert continued is not None
        assert continued.start_offset == 80
        assert continued.content == content[80:160]
        with pytest.raises(ValueError, match="canonical decimal"):
            service.read_full_document_range(
                user_id=user_id,
                document_id=document["id"],
                cursor="080",
                full_document_max_chars=100,
                range_chars=80,
            )
    finally:
        session_generator.close()


def test_many_chunk_full_document_uses_distributed_bounded_citations(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "full-many-chunks@example.com")
    kb_id = _create_knowledge_base(client, "Many chunk KB")
    content = "\n\n".join(
        f"## Section {index}\nRequirement {index}: preserve distributed provenance."
        for index in range(190)
    )
    document = _create_document(
        client,
        json={
            "title": "Many Chunk Source",
            "content": content,
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        persisted_chunks = list(
            db.scalars(
                select(DocumentChunkModel)
                .where(DocumentChunkModel.document_id == document["id"])
                .order_by(DocumentChunkModel.ordinal)
            ).all()
        )
        assert len(persisted_chunks) > 100

        read_result = RetrievalService(db).read_full_document_range(
            user_id=user_id,
            document_id=document["id"],
        )
        assert read_result is not None
        assert read_result.complete is True
        assert len(read_result.retrieved_chunks) == 100
        sampled_chunks = [item.chunk for item in read_result.retrieved_chunks]
        assert sampled_chunks[0].id == persisted_chunks[0].id
        assert sampled_chunks[-1].id == persisted_chunks[-1].id
        assert [chunk.ordinal for chunk in sampled_chunks] == sorted(
            {chunk.ordinal for chunk in sampled_chunks}
        )
    finally:
        session_generator.close()

    conversation_id = client.post("/conversations", json={"title": "Many chunk full read"}).json()[
        "id"
    ]
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Many Chunk Source 문서를 모두 읽고 내용을 요약해줘",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["document_coverage"]["mode"] == "complete"
    assert payload["citations"] == []
    assert len(payload["consulted_sources"]) == 100
    assert "I couldn't find enough relevant authorized document evidence" not in payload["reply"]


def test_explicit_document_read_permission_allows_full_document_without_kb_access(
    monkeypatch,
) -> None:  # noqa: ANN001
    owner = _client(monkeypatch)
    reader = _client(monkeypatch)
    _signup_login(owner, "full-permission-owner@example.com")
    reader_id = _signup_login(reader, "full-permission-reader@example.com")
    kb_id = _create_knowledge_base(owner, "Explicit permission KB")
    document = _create_document(
        owner,
        json={
            "title": "Shared Exact Source",
            "content": "Explicitly granted full-document evidence.",
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert owner.post(f"/documents/{document['id']}/ingest").status_code == 200
    grant = owner.patch(
        f"/documents/{document['id']}/permissions",
        json={"user_id": reader_id, "can_read": True},
    )
    assert grant.status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        service = RetrievalService(db)
        resolution = service.resolve_full_document_target(
            user_id=reader_id,
            query="Review the entire document Shared Exact Source.",
        )
        assert resolution.target is not None
        assert resolution.target.document_id == document["id"]
        read_result = service.read_full_document_range(
            user_id=reader_id,
            document_id=document["id"],
        )
        assert read_result is not None
        assert read_result.complete is True
        assert "Explicitly granted" in read_result.content
    finally:
        session_generator.close()


def test_ambient_system_document_is_never_a_full_document_target(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "full-system-exclusion@example.com")
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        system_kb = KnowledgeBaseModel(
            name="Ambient System KB",
            scope=KnowledgeBaseScope.SYSTEM.value,
            owner_user_id=user_id,
            purpose=KnowledgeBasePurpose.STANDARD.value,
        )
        db.add(system_kb)
        db.flush()
        system_document = DocumentModel(
            title="Hidden System Source",
            content="Ambient system content must never be fully disclosed.",
            owner_user_id=user_id,
            knowledge_base_id=system_kb.id,
        )
        db.add(system_document)
        db.commit()

        service = RetrievalService(db)
        resolution = service.resolve_full_document_target(
            user_id=user_id,
            query="Review the entire document Hidden System Source.",
        )
        assert resolution.target is None
        assert resolution.option_count == 0
        assert (
            service.read_full_document_range(
                user_id=user_id,
                document_id=system_document.id,
            )
            is None
        )
    finally:
        session_generator.close()


def test_small_full_document_run_is_refresh_safe_and_does_not_persist_raw_body(
    monkeypatch,
) -> None:  # noqa: ANN001
    marker = "FULL_DOCUMENT_PRIVATE_MARKER_7F3A"
    client = _client(monkeypatch)
    _signup_login(client, "full-complete@example.com")
    kb_id = _create_knowledge_base(client, "Complete document KB")
    document = _create_document(
        client,
        json={
            "title": "Complete Source",
            "content": f"Introduction. {marker}. Final requirement.",
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Complete coverage"}).json()[
        "id"
    ]

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Review the entire document and identify every requirement.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_coverage"] == {
        "mode": "complete",
        "document_id": document["id"],
        "title": "Complete Source",
        "source_filename": None,
        "start_offset": 0,
        "end_offset": len(f"Introduction. {marker}. Final requirement."),
        "total_chars": len(f"Introduction. {marker}. Final requirement."),
    }
    assert payload["citations"] == []
    assert {source["document_id"] for source in payload["consulted_sources"]} == {document["id"]}

    detail = client.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["document_coverage"] == payload["document_coverage"]
    events = client.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}/events")
    assert "full_document_read" in [event["event_type"] for event in events.json()]

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        raw_payloads = db.scalars(
            select(AgentEventModel.payload_json).where(AgentEventModel.run_id == payload["run_id"])
        ).all()
        assert marker not in "\n".join(raw_payloads)
    finally:
        session_generator.close()


def test_full_document_body_never_enters_checkpoint_state(monkeypatch) -> None:  # noqa: ANN001
    marker = "CHECKPOINT_PRIVATE_FULL_BODY_4C91"
    client = _client(monkeypatch)
    user_id = _signup_login(client, "full-checkpoint@example.com")
    kb_id = _create_knowledge_base(client, "Checkpoint safety KB")
    document = _create_document(
        client,
        json={
            "title": "Checkpoint Safe Source",
            "content": f"Start. {marker}. End.",
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        captured_context: list[dict[str, object]] = []

        class CapturingProvider:
            def compose_reply(self, **kwargs):  # noqa: ANN003, ANN201
                captured_context.extend(kwargs["retrieved_context"])
                return "Constant full-document answer."

        import my_agents.agents.general_assistant.graph as graph_module

        monkeypatch.setattr(graph_module, "get_response_provider", lambda: CapturingProvider())
        checkpointer = InMemorySaver(serde=checkpoint_serializer())
        graph = build_graph(checkpointer=checkpointer)
        state = {
            **graph_state(
                "Review the entire document and identify every requirement.",
                user_id=user_id,
                conversation_id="checkpoint-full-document",
            ),
            "run_id": "run-full-checkpoint",
        }
        selection_context = KnowledgeBaseSelectionContext(
            mode="selected",
            knowledge_base_ids=(kb_id,),
            resolved_count=1,
            resolved_knowledge_base_ids=(kb_id,),
        )
        config = {"configurable": {"thread_id": "run-full-checkpoint"}}
        result = graph.invoke(
            state,
            config=config,
            context={
                "user_id": user_id,
                "rag_runtime": SqlAlchemyRagAgentRuntime(db),
                "knowledge_base_selection": selection_context,
                "retrieval_source_decider": FakeRetrievalSourceDecider(source="knowledge_base"),
                "full_document_max_chars": 24_000,
                "full_document_range_chars": 12_000,
            },
        )
        assert result["document_coverage"]["mode"] == "complete"
        assert marker in str(captured_context[0]["snippet"])
        assert result["reply"] == "Constant full-document answer."
        checkpoint_values = [snapshot.values for snapshot in graph.get_state_history(config)]
        assert marker not in repr(checkpoint_values)
    finally:
        session_generator.close()


def test_large_full_document_run_is_partial_and_streams_honest_disclosure(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_FULL_DOCUMENT_MAX_CHARS", "4000")
    monkeypatch.setenv("MY_AGENTS_FULL_DOCUMENT_RANGE_CHARS", "2000")
    client = _client(monkeypatch)
    _signup_login(client, "full-partial@example.com")
    kb_id = _create_knowledge_base(client, "Partial document KB")
    content = "\n\n".join(f"Requirement {index}: " + ("detail " * 20) for index in range(60))
    assert len(content) > 4000
    document = _create_document(
        client,
        json={"title": "Large Source", "content": content, "knowledge_base_id": kb_id},
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Partial coverage"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={
            "message": "Review the entire document and list all requirements.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    assert completed["document_coverage"]["mode"] == "partial"
    assert completed["document_coverage"]["start_offset"] == 0
    assert completed["document_coverage"]["end_offset"] == 2000
    assert completed["document_coverage"]["total_chars"] == len(content)
    assert completed["reply"].startswith("Partial-review notice:")
    streamed_reply = "".join(
        event["data"]["delta"] for event in events if event["event"] == "answer_delta"
    )
    assert streamed_reply == completed["reply"]
    assert any(event["event"] == "full_document_read" for event in events)

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        cited_chunk_ids = db.scalars(
            select(CitationModel.chunk_id).where(CitationModel.run_id == completed["run_id"])
        ).all()
        cited_chunks = db.scalars(
            select(DocumentChunkModel).where(DocumentChunkModel.id.in_(cited_chunk_ids))
        ).all()
        assert cited_chunks
        assert all(chunk.start_offset < 2000 for chunk in cited_chunks)
    finally:
        session_generator.close()


def test_streaming_full_document_reread_downgrade_uses_final_graph_context(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "full-stream-toctou@example.com")
    kb_id = _create_knowledge_base(client, "Streaming TOCTOU KB")
    document = _create_document(
        client,
        json={
            "title": "Streaming TOCTOU Source",
            "content": "Requirement one. Requirement two.",
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
    read_calls = _make_document_stale_after_prepared_read(
        monkeypatch,
        document_id=document["id"],
    )
    conversation_id = client.post("/conversations", json={"title": "Streaming TOCTOU"}).json()["id"]

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/stream",
        json={
            "message": "Review the entire document and identify every requirement.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    event_names = [event["event"] for event in events]
    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    answer_composed = next(event["data"] for event in events if event["event"] == "answer_composed")
    assert len(read_calls) == 2
    assert event_names.count("retrieval_completed") == 1
    assert "full_document_read" not in event_names
    assert answer_composed["insufficient_evidence"] is True
    assert completed["document_coverage"] is None
    assert completed["citations"] == []
    assert completed["consulted_sources"] == []
    assert "enough relevant authorized document evidence" in completed["reply"]

    persisted = client.get(
        f"/conversations/{conversation_id}/runs/{completed['run_id']}/events"
    ).json()
    assert [event["event_type"] for event in persisted].count("retrieval_completed") == 1
    assert all(event["event_type"] != "full_document_read" for event in persisted)


def test_full_document_selection_interrupt_resumes_exact_document(monkeypatch) -> None:  # noqa: ANN001
    graph = build_graph(
        checkpointer=InMemorySaver(serde=checkpoint_serializer()),
        document_selection_hitl_enabled=True,
    )
    client = _client(monkeypatch)
    client.app.dependency_overrides[get_graph_runner] = lambda: graph
    _signup_login(client, "full-selection@example.com")
    kb_id = _create_knowledge_base(client, "Full selection KB")
    documents = []
    for title in ("First Source", "Second Source"):
        document = _create_document(
            client,
            json={
                "title": title,
                "content": f"{title} complete evidence.",
                "knowledge_base_id": kb_id,
            },
        ).json()
        assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
        documents.append(document)
    conversation_id = client.post("/conversations", json={"title": "Full selection"}).json()["id"]

    pending_response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Review this entire document from beginning to end.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert pending_response.status_code == 202
    pending = pending_response.json()

    resumed = client.post(
        f"/conversations/{conversation_id}/runs/{pending['run_id']}/resume",
        json={
            "schema_version": 1,
            "interaction_id": pending["interaction"]["interaction_id"],
            "type": "document_selection",
            "document_id": documents[1]["id"],
        },
    )
    assert resumed.status_code == 200
    completed = resumed.json()
    assert completed["document_coverage"]["document_id"] == documents[1]["id"]
    assert completed["citations"] == []
    assert {source["document_id"] for source in completed["consulted_sources"]} == {
        documents[1]["id"]
    }


def test_full_document_resume_stream_preserves_coverage_event_and_refresh_parity(
    monkeypatch,
) -> None:  # noqa: ANN001
    graph = build_graph(
        checkpointer=InMemorySaver(serde=checkpoint_serializer()),
        document_selection_hitl_enabled=True,
    )
    client = _client(monkeypatch)
    client.app.dependency_overrides[get_graph_runner] = lambda: graph
    _signup_login(client, "full-resume-stream-coverage@example.com")
    kb_id = _create_knowledge_base(client, "Resume stream coverage KB")
    documents = []
    for title in ("Resume First Source", "Resume Second Source"):
        document = _create_document(
            client,
            json={
                "title": title,
                "content": f"{title} complete evidence.",
                "knowledge_base_id": kb_id,
            },
        ).json()
        assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
        documents.append(document)
    conversation_id = client.post(
        "/conversations", json={"title": "Resume stream coverage"}
    ).json()["id"]
    pending_response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Review this entire document from beginning to end.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert pending_response.status_code == 202
    pending = pending_response.json()

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/{pending['run_id']}/resume/stream",
        json={
            "schema_version": 1,
            "interaction_id": pending["interaction"]["interaction_id"],
            "type": "document_selection",
            "document_id": documents[1]["id"],
        },
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    event_names = [event["event"] for event in events]
    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    full_document_read = next(
        event["data"] for event in events if event["event"] == "full_document_read"
    )
    assert event_names.count("retrieval_completed") == 1
    assert event_names.count("full_document_read") == 1
    assert completed["document_coverage"]["mode"] == "complete"
    assert completed["document_coverage"]["document_id"] == documents[1]["id"]
    assert {
        key: full_document_read[key] for key in DocumentCoverageResponse.model_fields
    } == completed["document_coverage"]
    assert full_document_read["latency_ms"] >= 0
    assert {source["document_id"] for source in completed["consulted_sources"]} == {
        documents[1]["id"]
    }

    detail = client.get(f"/conversations/{conversation_id}/runs/{pending['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["document_coverage"] == completed["document_coverage"]


def test_full_document_resume_stream_reread_downgrade_uses_final_graph_context(
    monkeypatch,
) -> None:  # noqa: ANN001
    graph = build_graph(
        checkpointer=InMemorySaver(serde=checkpoint_serializer()),
        document_selection_hitl_enabled=True,
    )
    client = _client(monkeypatch)
    client.app.dependency_overrides[get_graph_runner] = lambda: graph
    _signup_login(client, "full-resume-stream-toctou@example.com")
    kb_id = _create_knowledge_base(client, "Resume stream TOCTOU KB")
    documents = []
    for title in ("TOCTOU First Source", "TOCTOU Second Source"):
        document = _create_document(
            client,
            json={
                "title": title,
                "content": f"{title} complete evidence.",
                "knowledge_base_id": kb_id,
            },
        ).json()
        assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
        documents.append(document)
    conversation_id = client.post("/conversations", json={"title": "Resume stream TOCTOU"}).json()[
        "id"
    ]
    pending_response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Review this entire document from beginning to end.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert pending_response.status_code == 202
    pending = pending_response.json()
    read_calls = _make_document_stale_after_prepared_read(
        monkeypatch,
        document_id=documents[1]["id"],
    )

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/runs/{pending['run_id']}/resume/stream",
        json={
            "schema_version": 1,
            "interaction_id": pending["interaction"]["interaction_id"],
            "type": "document_selection",
            "document_id": documents[1]["id"],
        },
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    event_names = [event["event"] for event in events]
    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    answer_composed = next(event["data"] for event in events if event["event"] == "answer_composed")
    assert len(read_calls) == 2
    assert event_names.count("retrieval_completed") == 1
    assert "full_document_read" not in event_names
    assert answer_composed["insufficient_evidence"] is True
    assert completed["document_coverage"] is None
    assert completed["citations"] == []
    assert completed["consulted_sources"] == []
    assert "enough relevant authorized document evidence" in completed["reply"]


def test_full_document_replay_preserves_target_and_never_substitutes_after_delete(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "full-replay@example.com")
    kb_id = _create_knowledge_base(client, "Full replay KB")
    original = _create_document(
        client,
        json={
            "title": "Original Full Source",
            "content": "Original-only complete evidence.",
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert client.post(f"/documents/{original['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Full replay"}).json()["id"]
    original_run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Review the entire document and identify every requirement.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert original_run.status_code == 200

    distractor = _create_document(
        client,
        json={
            "title": "Later Distractor",
            "content": "Distractor evidence must never replace the original.",
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert client.post(f"/documents/{distractor['id']}/ingest").status_code == 200
    assistant_message_id = client.get(f"/conversations/{conversation_id}/messages").json()[-1]["id"]

    replay = client.post(
        f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay",
        json={},
    )
    assert replay.status_code == 200
    assert replay.json()["document_coverage"]["document_id"] == original["id"]
    assert replay.json()["citations"] == []
    assert {source["document_id"] for source in replay.json()["consulted_sources"]} == {
        original["id"]
    }

    latest_assistant_id = client.get(f"/conversations/{conversation_id}/messages").json()[-1]["id"]
    assert client.delete(f"/documents/{original['id']}").status_code == 204
    unavailable = client.post(
        f"/conversations/{conversation_id}/messages/{latest_assistant_id}/replay",
        json={},
    )
    assert unavailable.status_code == 200
    unavailable_payload = unavailable.json()
    assert unavailable_payload["document_coverage"] is None
    assert unavailable_payload["citations"] == []
    assert unavailable_payload["consulted_sources"] == []
    assert unavailable_payload["warnings"][0]["missing_document_ids"] == [original["id"]]
    assert "Later Distractor" not in unavailable_payload["reply"]


def test_full_document_replay_stream_reread_downgrade_uses_final_graph_context(
    monkeypatch,
) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    _signup_login(client, "full-replay-stream-toctou@example.com")
    kb_id = _create_knowledge_base(client, "Replay stream TOCTOU KB")
    document = _create_document(
        client,
        json={
            "title": "Replay Stream TOCTOU Source",
            "content": "Original complete evidence for replay.",
            "knowledge_base_id": kb_id,
        },
    ).json()
    assert client.post(f"/documents/{document['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Replay stream TOCTOU"}).json()[
        "id"
    ]
    original_run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Review the entire document and identify every requirement.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )
    assert original_run.status_code == 200
    assistant_message_id = client.get(f"/conversations/{conversation_id}/messages").json()[-1]["id"]
    read_calls = _make_document_stale_after_prepared_read(
        monkeypatch,
        document_id=document["id"],
    )

    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages/{assistant_message_id}/replay/stream",
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    event_names = [event["event"] for event in events]
    completed = next(event["data"] for event in events if event["event"] == "run_completed")
    answer_composed = next(event["data"] for event in events if event["event"] == "answer_composed")
    assert len(read_calls) == 2
    assert event_names.count("retrieval_completed") == 1
    assert "full_document_read" not in event_names
    assert answer_composed["insufficient_evidence"] is True
    assert completed["document_coverage"] is None
    assert completed["citations"] == []
    assert completed["consulted_sources"] == []
    assert "enough relevant authorized document evidence" in completed["reply"]

    persisted = client.get(
        f"/conversations/{conversation_id}/runs/{completed['run_id']}/events"
    ).json()
    assert [event["event_type"] for event in persisted].count("retrieval_completed") == 1
    assert all(event["event_type"] != "full_document_read" for event in persisted)


def test_fallback_deltas_reconstruct_partial_disclosure() -> None:
    reply = "Partial-review notice: bounded coverage.\n\nAnswer."
    assert "".join(fallback_answer_deltas(reply)) == reply
