"""Permission-aware RAG and graph expansion tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import my_agents.knowledge.extraction as extraction_module
import my_agents.knowledge.retrieval as retrieval_module
from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.knowledge.metadata_enrichment import (
    DocumentMetadataProfile,
    GeneratedDocumentMetadata,
    build_vector_search_text,
)
from my_agents.knowledge.models import DocumentChunkModel, DocumentMetadataProfileModel
from my_agents.knowledge.retrieval import RetrievalService, _postgres_vector_authorized_statement
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email
from .rag_spy_helpers import rag_update_for_spy


class RagSpyGraph:
    """Graph spy that keeps product RAG composition deterministic in tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict, **kwargs: Any) -> dict:  # noqa: A002 - matches LangGraph API
        rag_update = rag_update_for_spy(input, kwargs)
        self.calls.append({**input, **rag_update})
        return {
            **rag_update,
            "reply": "graph reply without hidden document text",
            "route": RouteDecision(label="general_assistant", explanation="spy route"),
        }


class SemanticFakeEmbeddingProvider:
    provider = "fake"
    model = "semantic-fake-v1"
    dimensions = 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        normalized = text.casefold()
        if any(term in normalized for term in ("automobile", "vehicle", "car", "cars")):
            return [1.0, 0.0]
        if any(term in normalized for term in ("pastry", "baking", "bread")):
            return [0.0, 1.0]
        return [0.5, 0.5]


class MetadataFocusedEmbeddingProvider(SemanticFakeEmbeddingProvider):
    provider = "metadata-focused-fake"
    model = "metadata-focused-fake-v1"

    def _vector(self, text: str) -> list[float]:
        normalized = text.casefold()
        if any(term in normalized for term in ("oncology", "cancer", "tumor")):
            return [1.0, 0.0]
        if any(term in normalized for term in ("visit schedule", "dosing table")):
            return [0.0, 1.0]
        return [0.0, 1.0]


class StaticMetadataGenerator:
    name = "openai-test-double"
    model = "metadata-test-model"

    def generate(self, document):  # noqa: ANN001
        metadata = GeneratedDocumentMetadata(
            title="Oncology clinical trial protocol",
            description=(
                "Vector search metadata for oncology, cancer therapy, tumor response, "
                "trial eligibility, dosing, adverse events, and protocol lookup."
            ),
            summary=(
                "Search-oriented profile: oncology clinical trial protocol covering cancer "
                "treatment eligibility, treatment schedule, safety monitoring, and outcomes."
            ),
            keywords=[
                "oncology",
                "cancer therapy",
                "tumor response",
                "clinical trial protocol",
                "eligibility criteria",
            ],
            topics=["oncology trial", "protocol retrieval"],
            entities=["NCT06159946_Prot_000"],
            language="en",
            confidence="high",
        )
        return DocumentMetadataProfile(
            metadata=metadata,
            search_text=build_vector_search_text(
                metadata,
                source_filename=document.source_filename,
                explicit_title=document.title,
            ),
            generator=self.name,
            model=self.model,
        )


def _client(monkeypatch, graph: RagSpyGraph | None = None) -> TestClient:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    app = create_app()
    if graph is not None:
        app.dependency_overrides[get_graph_runner] = lambda: graph
    return TestClient(app)


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


def _create_personal_knowledge_base(client: TestClient, name: str = "Test KB") -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_conversation(client: TestClient, title: str = "RAG") -> str:
    response = client.post("/conversations", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def _create_knowledge_base(client: TestClient, name: str = "Test KB") -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_document(client: TestClient, *, json: dict):  # noqa: ANN201
    payload = dict(json)
    payload.setdefault("knowledge_base_id", _create_knowledge_base(client))
    return client.post("/documents", json=payload)


def _duplicate_first_chunk(document_id: str) -> str:
    session_generator = get_database_session()
    db = next(session_generator)
    try:
        original = db.scalar(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.document_id == document_id)
            .order_by(DocumentChunkModel.ordinal)
        )
        assert original is not None
        duplicate = DocumentChunkModel(
            document_id=original.document_id,
            extraction_run_id=original.extraction_run_id,
            ordinal=original.ordinal,
            content=original.content,
            start_offset=original.start_offset,
            end_offset=original.end_offset,
            source_page=original.source_page,
            embedding_json=original.embedding_json,
        )
        db.add(duplicate)
        db.commit()
        db.refresh(duplicate)
        return duplicate.id
    finally:
        session_generator.close()


def test_chat_run_cites_only_authorized_personal_knowledge(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "rag-owner@example.com")
    _signup_login(outsider, "rag-outsider@example.com")
    kb_id = _create_personal_knowledge_base(owner, "RAG Owner KB")

    private_phrase = "Phoenix Retrieval Kernel"
    document = _create_document(
        owner,
        json={
            "title": "Private RAG Plan",
            "content": f"{private_phrase} uses LangGraph for authorized answers.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    ingest = owner.post(f"/documents/{document.json()['id']}/ingest")
    assert ingest.status_code == 200

    owner_conversation_id = _create_conversation(owner, "Owner RAG")
    owner_run = owner.post(
        f"/conversations/{owner_conversation_id}/runs",
        json={"message": "How does the Phoenix retrieval work?"},
    )
    assert owner_run.status_code == 200
    owner_payload = owner_run.json()
    assert owner_payload["citations"]
    assert owner_payload["citations"][0]["document_id"] == document.json()["id"]
    assert private_phrase in owner_payload["citations"][0]["snippet"]
    assert owner_payload["reply"] == "graph reply without hidden document text"
    assert graph.calls[-1]["retrieved_chunk_ids"]
    owner_detail = owner.get(
        f"/conversations/{owner_conversation_id}/runs/{owner_payload['run_id']}"
    )
    assert owner_detail.status_code == 200
    assert owner_detail.json() == owner_payload

    outsider_conversation_id = _create_conversation(outsider, "Outsider RAG")
    outsider_run = outsider.post(
        f"/conversations/{outsider_conversation_id}/runs",
        json={"message": "How does the Phoenix retrieval work?"},
    )
    assert outsider_run.status_code == 200
    outsider_payload = outsider_run.json()
    assert outsider_payload["citations"] == []
    assert private_phrase not in outsider_payload["reply"]
    assert graph.calls[-1]["retrieved_chunk_ids"] == []


def test_broad_resume_question_uses_recent_authorized_document(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "resume-owner@example.com")
    _signup_login(outsider, "resume-outsider@example.com")
    kb_id = _create_personal_knowledge_base(owner, "Resume KB")

    resume_phrase = "Heecheon Park builds FastAPI LangGraph product systems"
    document = _create_document(
        owner,
        json={
            "title": "Resume 2026",
            "content": f"{resume_phrase} with permission-aware document retrieval.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert owner.post(f"/documents/{document.json()['id']}/ingest").status_code == 200

    owner_conversation_id = _create_conversation(owner, "Resume chat")
    owner_run = owner.post(
        f"/conversations/{owner_conversation_id}/runs",
        json={"message": "Tell me about me from my uploaded resume."},
    )

    assert owner_run.status_code == 200
    owner_payload = owner_run.json()
    assert owner_payload["citations"]
    assert owner_payload["citations"][0]["document_id"] == document.json()["id"]
    assert resume_phrase in owner_payload["citations"][0]["snippet"]
    assert owner_payload["reply"] == "graph reply without hidden document text"
    assert graph.calls[-1]["retrieved_chunk_ids"]
    assert graph.calls[-1]["retrieved_context"][0]["title"] == "Resume 2026"
    assert resume_phrase in graph.calls[-1]["retrieved_context"][0]["snippet"]
    graph_call_count = len(graph.calls)

    outsider_conversation_id = _create_conversation(outsider, "No resume leak")
    outsider_run = outsider.post(
        f"/conversations/{outsider_conversation_id}/runs",
        json={"message": "Tell me about me from my uploaded resume."},
    )

    assert outsider_run.status_code == 200
    outsider_payload = outsider_run.json()
    assert outsider_payload["citations"] == []
    assert resume_phrase not in outsider_payload["reply"]
    assert "enough relevant authorized document evidence" in outsider_payload["reply"]
    assert len(graph.calls) == graph_call_count + 1
    assert graph.calls[-1]["rag_halt_before_response"] is True
    assert graph.calls[-1]["retrieval_route"] == "retrieval_required"
    assert graph.calls[-1]["retrieved_context"] == []


def test_semantic_vector_retrieval_after_permission_filtering(monkeypatch) -> None:  # noqa: ANN001
    fake_embeddings = SemanticFakeEmbeddingProvider()
    monkeypatch.setattr(extraction_module, "get_embedding_provider", lambda: fake_embeddings)
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: fake_embeddings)
    graph = RagSpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "semantic-owner@example.com")
    _signup_login(outsider, "semantic-outsider@example.com")
    kb_id = _create_personal_knowledge_base(owner, "Semantic KB")

    vehicle_doc = _create_document(
        owner,
        json={
            "title": "Vehicle Notes",
            "content": "Automobile maintenance schedule uses quarterly inspections.",
            "knowledge_base_id": kb_id,
        },
    )
    pastry_doc = _create_document(
        owner,
        json={
            "title": "Pastry Notes",
            "content": "Pastry dough proofing depends on warm kitchen timing.",
            "knowledge_base_id": kb_id,
        },
    )
    assert vehicle_doc.status_code == 201
    assert pastry_doc.status_code == 201
    assert owner.post(f"/documents/{vehicle_doc.json()['id']}/ingest").status_code == 200
    assert owner.post(f"/documents/{pastry_doc.json()['id']}/ingest").status_code == 200

    owner_conversation_id = _create_conversation(owner, "Semantic owner")
    owner_run = owner.post(
        f"/conversations/{owner_conversation_id}/runs",
        json={"message": "What does my uploaded document say about cars?"},
    )

    assert owner_run.status_code == 200
    owner_payload = owner_run.json()
    assert owner_payload["citations"]
    assert owner_payload["citations"][0]["document_id"] == vehicle_doc.json()["id"]
    assert "Automobile maintenance" in owner_payload["citations"][0]["snippet"]
    assert owner_payload["reply"] == "graph reply without hidden document text"
    assert graph.calls[-1]["retrieved_context"][0]["title"] == "Vehicle Notes"
    graph_call_count = len(graph.calls)

    outsider_conversation_id = _create_conversation(outsider, "Semantic outsider")
    outsider_run = outsider.post(
        f"/conversations/{outsider_conversation_id}/runs",
        json={"message": "What does my uploaded document say about cars?"},
    )

    assert outsider_run.status_code == 200
    outsider_payload = outsider_run.json()
    assert outsider_payload["citations"] == []
    assert "enough relevant authorized document evidence" in outsider_payload["reply"]
    assert len(graph.calls) == graph_call_count + 1
    assert graph.calls[-1]["rag_halt_before_response"] is True
    assert graph.calls[-1]["retrieval_route"] == "retrieval_required"
    assert graph.calls[-1]["retrieved_context"] == []


def test_retrieval_dedupes_historical_duplicate_chunks(monkeypatch) -> None:  # noqa: ANN001
    client = _client(monkeypatch)
    user_id = _signup_login(client, "dedupe-owner@example.com")
    kb_id = _create_personal_knowledge_base(client, "Dedupe KB")
    document = _create_document(
        client,
        json={
            "title": "Duplicate Chunk Notes",
            "content": "DuplicateAlpha retrieval fact.\n\nDuplicateBeta retrieval fact.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    document_id = document.json()["id"]
    assert client.post(f"/documents/{document_id}/ingest").status_code == 200
    _duplicate_first_chunk(document_id)

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        rows = db.scalars(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.document_id == document_id)
            .order_by(DocumentChunkModel.ordinal, DocumentChunkModel.id)
        ).all()
        assert [chunk.ordinal for chunk in rows].count(0) == 2

        results = RetrievalService(db).retrieve_scoped(
            user_id=user_id,
            query="What does DuplicateAlpha say?",
            limit=5,
            knowledge_base_ids=[kb_id],
        )
    finally:
        session_generator.close()

    result_ordinals = [item.chunk.ordinal for item in results]
    result_contents = [item.chunk.content for item in results]
    assert result_ordinals.count(0) == 1
    assert len(result_contents) == len(set(result_contents))


def test_summary_query_gets_broader_small_document_coverage(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "overview-owner@example.com")
    kb_id = _create_personal_knowledge_base(client, "Overview KB")
    document = _create_document(
        client,
        json={
            "title": "Small Overview Notes",
            "content": "AlphaOverview first feature.\n\n"
            "BetaOverview second feature.\n\n"
            "GammaOverview third feature.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = _create_conversation(client, "Overview")

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Summarize what my uploaded document says about AlphaOverview.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )

    assert response.status_code == 200
    snippets = [context["snippet"] for context in graph.calls[-1]["retrieved_context"]]
    assert any("AlphaOverview" in snippet for snippet in snippets)
    assert any("BetaOverview" in snippet for snippet in snippets)
    assert any("GammaOverview" in snippet for snippet in snippets)


def test_rag_reply_does_not_prepend_clipped_markdown_snippet(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "clipped-snippet-owner@example.com")
    kb_id = _create_personal_knowledge_base(client, "Markdown Snippet KB")
    document = _create_document(
        client,
        json={
            "title": "GreetSchool README",
            "content": "- [x] 학생 엑셀 다운로드 UI\n- [x] 출석 리포트\n- [x] 학부모 알림\n",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = _create_conversation(client, "No clipped prefix")

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "Summarize my uploaded GreetSchool README.",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "graph reply without hidden document text"
    assert "- [x] 학생 엑셀 다운로드 UI\n- [" not in payload["reply"]
    assert any("학생 엑셀 다운로드 UI" in citation["snippet"] for citation in payload["citations"])


def test_selected_kb_vector_retrieval_respects_scope(monkeypatch) -> None:  # noqa: ANN001
    fake_embeddings = SemanticFakeEmbeddingProvider()
    monkeypatch.setattr(extraction_module, "get_embedding_provider", lambda: fake_embeddings)
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: fake_embeddings)
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "semantic-selected@example.com")
    vehicle_kb = _create_personal_knowledge_base(client, "Vehicle KB")
    pastry_kb = _create_personal_knowledge_base(client, "Pastry KB")

    vehicle_doc = _create_document(
        client,
        json={
            "title": "Vehicle Scope Notes",
            "content": "Automobile maintenance schedule uses quarterly inspections.",
            "knowledge_base_id": vehicle_kb,
        },
    )
    pastry_doc = _create_document(
        client,
        json={
            "title": "Pastry Scope Notes",
            "content": "Pastry dough proofing depends on warm kitchen timing.",
            "knowledge_base_id": pastry_kb,
        },
    )
    assert vehicle_doc.status_code == 201
    assert pastry_doc.status_code == 201
    assert client.post(f"/documents/{vehicle_doc.json()['id']}/ingest").status_code == 200
    assert client.post(f"/documents/{pastry_doc.json()['id']}/ingest").status_code == 200
    conversation_id = _create_conversation(client, "Selected semantic")

    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "What does my uploaded document say about cars?",
            "knowledge_base_selection": {
                "mode": "selected",
                "knowledge_base_ids": [vehicle_kb],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["knowledge_base_selection"] == {
        "mode": "selected",
        "knowledge_base_ids": [vehicle_kb],
    }
    assert payload["resolved_knowledge_base_count"] == 1
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {vehicle_kb}
    assert {citation["document_id"] for citation in payload["citations"]} == {
        vehicle_doc.json()["id"]
    }
    assert any("Automobile maintenance" in citation["snippet"] for citation in payload["citations"])
    assert "Pastry dough" not in payload["reply"]
    assert {context["document_id"] for context in graph.calls[-1]["retrieved_context"]} == {
        vehicle_doc.json()["id"]
    }
    assert {context["source"] for context in graph.calls[-1]["retrieved_context"]} == {
        "semantic_vector"
    }


def test_generated_metadata_profile_retrieves_when_body_lacks_search_terms(
    monkeypatch,
) -> None:  # noqa: ANN001
    fake_embeddings = MetadataFocusedEmbeddingProvider()
    monkeypatch.setattr(extraction_module, "get_embedding_provider", lambda: fake_embeddings)
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: fake_embeddings)
    monkeypatch.setattr(
        extraction_module,
        "build_document_metadata_generator",
        lambda _settings: StaticMetadataGenerator(),
    )
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    user_id = _signup_login(client, "metadata-profile-owner@example.com")
    kb_id = _create_personal_knowledge_base(client, "Metadata Profile KB")
    document = _create_document(
        client,
        json={
            "title": "Protocol Schedule Notes",
            "content": "Visit schedule and dosing table are stored here without disease terms.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    document_id = document.json()["id"]
    assert client.post(f"/documents/{document_id}/ingest").status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        profile = db.scalar(
            select(DocumentMetadataProfileModel).where(
                DocumentMetadataProfileModel.document_id == document_id
            )
        )
        assert profile is not None
        assert profile.generator == "openai-test-double"
        assert "oncology" in profile.search_text
        results = RetrievalService(db).retrieve_scoped(
            user_id=user_id,
            query="Find my oncology cancer protocol",
            limit=5,
            knowledge_base_ids=[kb_id],
        )
    finally:
        session_generator.close()

    assert results
    assert results[0].document.id == document_id
    assert results[0].source == "document_metadata_profile"

    conversation_id = _create_conversation(client, "Metadata profile retrieval")
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "What does my uploaded oncology protocol say?",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["citations"][0]["document_id"] == document_id
    assert graph.calls[-1]["retrieved_context"][0]["source"] == "document_metadata_profile"

    events = client.get(f"/conversations/{conversation_id}/runs/{payload['run_id']}/events")
    retrieval_event = next(
        event for event in events.json() if event["event_type"] == "retrieval_completed"
    )
    assert retrieval_event["payload"]["document_metadata_profile_count"] >= 1


def test_metadata_profile_match_injects_body_chunks_not_only_heading(
    monkeypatch,
) -> None:  # noqa: ANN001
    fake_embeddings = MetadataFocusedEmbeddingProvider()
    monkeypatch.setattr(extraction_module, "get_embedding_provider", lambda: fake_embeddings)
    monkeypatch.setattr(retrieval_module, "get_embedding_provider", lambda: fake_embeddings)
    monkeypatch.setattr(
        extraction_module,
        "build_document_metadata_generator",
        lambda _settings: StaticMetadataGenerator(),
    )
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    user_id = _signup_login(client, "metadata-profile-body-owner@example.com")
    kb_id = _create_personal_knowledge_base(client, "Metadata Body KB")
    document = _create_document(
        client,
        json={
            "title": "Protocol Schedule Notes",
            "content": (
                "# Protocol Schedule Notes\n\n"
                "Visit schedule and dosing table are stored here without disease terms.\n\n"
                "Participants return every six weeks for safety monitoring and outcome review."
            ),
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    document_id = document.json()["id"]
    assert client.post(f"/documents/{document_id}/ingest").status_code == 200

    session_generator = get_database_session()
    db = next(session_generator)
    try:
        results = RetrievalService(db).retrieve_scoped(
            user_id=user_id,
            query="Find my oncology cancer protocol",
            limit=5,
            knowledge_base_ids=[kb_id],
        )
    finally:
        session_generator.close()

    assert results
    assert any(
        result.source == "document_metadata_profile"
        and any(
            line.strip() and not line.lstrip().startswith("#")
            for line in result.chunk.content.splitlines()
        )
        for result in results
    )
    assert any("Visit schedule and dosing table" in result.chunk.content for result in results)

    conversation_id = _create_conversation(client, "Metadata body retrieval")
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "What does my uploaded oncology protocol say?",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["citations"][0]["document_id"] == document_id
    assert any(
        "Visit schedule and dosing table" in context["snippet"]
        for context in graph.calls[-1]["retrieved_context"]
    )


def test_postgres_vector_statement_filters_permissions_before_vector_ordering() -> None:
    statement = _postgres_vector_authorized_statement(
        user_id="user-1",
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "document_chunks.embedding_vector <=>" in sql
    assert "document_chunks.embedding_vector IS NOT NULL" in sql
    assert "documents.owner_user_id" in sql
    assert "memberships.user_id" in sql
    assert "document_permissions.user_id" in sql
    assert "ORDER BY vector_distance" in sql

    scoped_statement = _postgres_vector_authorized_statement(
        user_id="user-1",
        query_embedding=[1.0, 0.0],
        limit=10,
        knowledge_base_ids=["kb-selected"],
    )
    scoped_sql = str(scoped_statement.compile(dialect=postgresql.dialect()))

    assert "documents.knowledge_base_id" in scoped_sql


def test_graph_expansion_adds_authorized_related_chunks(monkeypatch) -> None:  # noqa: ANN001
    graph = RagSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "rag-expansion@example.com")
    kb_id = _create_personal_knowledge_base(client, "Expansion KB")
    unselected_kb_id = _create_personal_knowledge_base(client, "Unselected Expansion KB")
    entity_mention_batch_calls: list[tuple[str, ...]] = []
    original_entity_mentions_for_chunks = retrieval_module._entity_mentions_for_chunks

    def track_entity_mention_batch(db, chunk_ids):  # noqa: ANN001
        batch = tuple(chunk_ids)
        entity_mention_batch_calls.append(batch)
        return original_entity_mentions_for_chunks(db, batch)

    monkeypatch.setattr(
        retrieval_module,
        "_entity_mentions_for_chunks",
        track_entity_mention_batch,
    )

    document = _create_document(
        client,
        json={
            "title": "LangGraph Expansion Notes",
            "content": "LangGraph retrieval matches AlphaQuery.\n\n"
            "LangGraph planner memory explains follow-up synthesis.",
            "knowledge_base_id": kb_id,
        },
    )
    unselected_document = _create_document(
        client,
        json={
            "title": "Unselected LangGraph Expansion Notes",
            "content": "LangGraph retrieval matches AlphaQuery.\n\n"
            "LangGraph unselected memory must stay outside selected scope.",
            "knowledge_base_id": unselected_kb_id,
        },
    )
    assert document.status_code == 201
    assert unselected_document.status_code == 201
    ingest = client.post(f"/documents/{document.json()['id']}/ingest")
    unselected_ingest = client.post(f"/documents/{unselected_document.json()['id']}/ingest")
    assert ingest.status_code == 200
    assert unselected_ingest.status_code == 200
    assert ingest.json()["chunk_count"] == 2
    assert unselected_ingest.json()["chunk_count"] == 2

    conversation_id = _create_conversation(client, "Expansion")
    response = client.post(
        f"/conversations/{conversation_id}/runs",
        json={
            "message": "AlphaQuery",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb_id]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    snippets = [citation["snippet"] for citation in payload["citations"]]
    assert payload["knowledge_base_selection"] == {
        "mode": "selected",
        "knowledge_base_ids": [kb_id],
    }
    assert payload["resolved_knowledge_base_count"] == 1
    assert len(snippets) == 2
    assert any("AlphaQuery" in snippet for snippet in snippets)
    assert any("planner memory" in snippet for snippet in snippets)
    assert all("unselected memory" not in snippet for snippet in snippets)
    assert {citation["knowledge_base_id"] for citation in payload["citations"]} == {kb_id}
    assert any("planner memory" in citation["snippet"] for citation in payload["citations"])
    assert "unselected memory" not in payload["reply"]
    assert len(graph.calls[-1]["retrieved_chunk_ids"]) == 2
    context_sources = {context["source"] for context in graph.calls[-1]["retrieved_context"]}
    assert "graph_expansion" in context_sources
    assert context_sources <= {"semantic_vector", "keyword_match", "graph_expansion"}
    assert {context["document_id"] for context in graph.calls[-1]["retrieved_context"]} == {
        document.json()["id"]
    }
    assert len(entity_mention_batch_calls) == 1
    assert all(chunk_id for chunk_id in entity_mention_batch_calls[0])
