"""Document preferences, authorized retrieval, and admission pinning stay offline."""

from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from my_agents.agents.general_assistant.graph import build_graph
from my_agents.agents.general_assistant.retrieval_gate import retrieval_preference_decision
from my_agents.agents.rag_agent import SqlAlchemyRagAgentRuntime
from my_agents.answer_worker import process_once
from my_agents.auth.models import UserModel
from my_agents.conversations.models import AgentRunModel
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.persistence.database import _sessionmaker_for_url, get_database_session
from my_agents.persistence.langgraph import checkpoint_serializer

from .test_assistant_model_preferences import ModelSpyGraph, ModelStreamingSpyGraph
from .test_conversations_api import (
    _assistant_message_id,
    _client,
    _create_document,
    _create_knowledge_base,
    _parse_sse,
    _signup_login,
)
from .test_durable_runs_api import admit, waiting_run
from .test_durable_runs_api import durable as durable
from .test_guest_access_api import _client as guest_client
from .test_guest_access_api import _guest_login


def _patch(client, **values):
    response = client.patch("/assistant/preferences", json=values)
    assert response.status_code == 200, response.text
    return response.json()


def test_document_preferences_patch_preserves_unspecified_fields_and_model_reset(monkeypatch):
    client = _client(monkeypatch)
    _signup_login(client, "document-preferences@example.com")
    before = client.get("/assistant/preferences").json()
    assert before["document_retrieval_priority"] == "automatic"
    assert before["document_ingestion_mode"] == "standard"
    _patch(client, assistant_model="gpt-6-astra")
    prefs = _patch(client, document_retrieval_priority="always")
    assert prefs["selected_model"] == "gpt-6-astra"
    prefs = _patch(client, document_ingestion_mode="ai_assisted")
    assert prefs["document_retrieval_priority"] == "always"
    assert prefs["selected_model"] == "gpt-6-astra"
    prefs = _patch(client, assistant_model="gpt-6-luna")
    assert prefs["selected_model"] == "gpt-6-luna"
    assert prefs["document_retrieval_priority"] == "always"
    assert prefs["document_ingestion_mode"] == "ai_assisted"
    prefs = _patch(client, assistant_model=None)
    assert prefs["selected_model"] is None
    assert prefs["document_retrieval_priority"] == "always"
    assert prefs["document_ingestion_mode"] == "ai_assisted"
    assert client.get("/assistant/preferences").json() == prefs
    other = _client(monkeypatch)
    _signup_login(other, "document-preferences-other@example.com")
    assert other.get("/assistant/preferences").json()["document_retrieval_priority"] == "automatic"


def test_preference_patch_openapi_has_no_defaults_that_generate_unrequested_changes(monkeypatch):
    client = _client(monkeypatch)
    schema = client.get("/openapi.json").json()["components"]["schemas"][
        "AssistantPreferencesPatchRequest"
    ]
    assert not schema.get("required")
    for name in ("assistant_model", "document_retrieval_priority", "document_ingestion_mode"):
        assert "default" not in schema["properties"][name]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"document_retrieval_priority": None},
        {"document_ingestion_mode": None},
        {"document_retrieval_priority": "unlikely"},
        {"document_ingestion_mode": "summarize"},
    ],
)
def test_invalid_document_preference_is_rejected_without_reset(monkeypatch, body):
    client = _client(monkeypatch)
    _signup_login(client, "invalid-document-preferences@example.com")
    before = _patch(client, document_retrieval_priority="on_request")
    assert client.patch("/assistant/preferences", json=body).status_code == 422
    assert client.get("/assistant/preferences").json() == before


def test_guests_ignore_document_preferences_even_if_present_in_db(monkeypatch):
    graph = ModelSpyGraph()
    client = guest_client(monkeypatch, graph=graph)
    login = _guest_login(client)
    sessions = get_database_session()
    db = next(sessions)
    try:
        user = db.get(UserModel, login["user"]["id"])
        user.document_retrieval_priority = "always"
        user.document_ingestion_mode = "ai_assisted"
        db.commit()
    finally:
        sessions.close()
    prefs = client.get("/assistant/preferences").json()
    assert prefs["customizable"] is False
    assert prefs["document_retrieval_priority"] == "automatic"
    assert prefs["document_ingestion_mode"] == "standard"
    for body in (
        {"document_retrieval_priority": "always"},
        {"document_ingestion_mode": "ai_assisted"},
    ):
        assert client.patch("/assistant/preferences", json=body).status_code == 403
    cid = client.post("/conversations", json={"title": "Guest preferences"}).json()["id"]
    response = client.post(f"/conversations/{cid}/runs", json={"message": "Hello"})
    assert response.status_code == 200
    assert graph.contexts[-1]["document_retrieval_priority"] == "automatic"


@pytest.mark.parametrize(
    ("priority", "query", "history", "selected", "expected"),
    [
        ("automatic", "What is RAG?", [], False, None),
        ("always", "What is RAG?", [], False, "knowledge_base"),
        ("always", "Don't use saved docs. What is RAG?", [], True, "bypass"),
        ("always", "저장된 문서를 사용하지 말고 알려줘", [], False, "bypass"),
        ("on_request", "What is RAG?", [], False, "bypass"),
        ("on_request", "What is RAG?", [], True, "knowledge_base"),
        ("on_request", "Compare architecture approaches", [], False, "bypass"),
        ("on_request", "Summarize my uploaded document", [], False, "knowledge_base"),
        (
            "on_request",
            "이미 저장해놓은 문서인데 문서 기반으로 알려줘",
            [],
            False,
            "knowledge_base",
        ),
        (
            "on_request",
            "What about routing?",
            ["Explain my uploaded document"],
            False,
            "knowledge_base",
        ),
        ("on_request", "What about routing?", ["Search the web for LangGraph"], False, "bypass"),
        ("on_request", "What is the weather?", ["Explain my uploaded document"], False, "bypass"),
        ("on_request", "What about web news?", ["Explain my uploaded document"], False, "bypass"),
        ("on_request", "Don't use documents", ["Explain my uploaded document"], True, "bypass"),
    ],
)
def test_preference_source_policy(priority, query, history, selected, expected):
    messages = []
    for text in history:
        messages.extend([HumanMessage(content=text), AIMessage(content="Prior answer")])
    messages.append(HumanMessage(content=query))
    selection = KnowledgeBaseSelectionContext(
        mode="selected" if selected else "all",
        knowledge_base_ids=("authorized-kb",) if selected else (),
        resolved_count=1,
        ambient_system_knowledge_base_ids=("system-kb",),
        ambient_system_knowledge_base_count=1,
    )
    result = retrieval_preference_decision(
        priority=priority, messages=messages, selection_context=selection
    )
    assert (result.source if result else None) == expected
    assert selection.ambient_system_knowledge_base_ids == ("system-kb",)


@pytest.mark.parametrize("priority", ["always", "on_request"])
def test_empty_selected_scope_never_widens_due_to_preference(priority):
    result = retrieval_preference_decision(
        priority=priority,
        messages=[HumanMessage(content="Search my documents")],
        selection_context=KnowledgeBaseSelectionContext(
            mode="selected", knowledge_base_ids=(), resolved_count=0
        ),
    )
    assert result.source == "bypass"


_BARE_DOCUMENT_COMMANDS = [
    ("Search documents for the state design principles.", True),
    ("Use documents to answer this question.", True),
    ("문서 검색해줘", True),
    ("문서 찾아보고 알려줘", True),
    ("Don't search documents for the state design principles.", False),
    ("Do not use documents to answer this question.", False),
    ("Never consult documents for this question.", False),
    ("Answer without using documents.", False),
    ("문서 검색하지 말고 알려줘", False),
    ("문서를 찾아보지 말고 알려줘", False),
    ("문서 안 찾아보고 알려줘", False),
]


@pytest.mark.parametrize("query,should_search", _BARE_DOCUMENT_COMMANDS)
def test_on_request_honors_bare_document_commands_and_negations(query, should_search):
    decision = retrieval_preference_decision(
        priority="on_request",
        messages=[HumanMessage(content=query)],
        selection_context=KnowledgeBaseSelectionContext(
            mode="all", knowledge_base_ids=(), resolved_count=1
        ),
    )
    assert decision.source == ("knowledge_base" if should_search else "bypass")
    if not should_search:
        always = retrieval_preference_decision(
            priority="always",
            messages=[HumanMessage(content=query)],
            selection_context=KnowledgeBaseSelectionContext(
                mode="all", knowledge_base_ids=(), resolved_count=1
            ),
        )
        assert always.source == "bypass"


@pytest.mark.parametrize("query,should_search", _BARE_DOCUMENT_COMMANDS)
def test_real_graph_on_request_obeys_bare_document_commands(monkeypatch, query, should_search):
    original = SqlAlchemyRagAgentRuntime.retrieve_context
    retrieval_queries = []

    def capture_retrieval(self, **kwargs):
        retrieval_queries.append(kwargs["message"])
        return original(self, **kwargs)

    monkeypatch.setattr(SqlAlchemyRagAgentRuntime, "retrieve_context", capture_retrieval)
    client = _client(monkeypatch, build_graph())
    _signup_login(client, "explicit-document-command@example.com")
    _document(client)
    _patch(client, document_retrieval_priority="on_request")
    cid = client.post("/conversations", json={"title": "Explicit document request"}).json()["id"]
    response = client.post(f"/conversations/{cid}/runs", json={"message": query})
    assert response.status_code == 200, response.text
    assert bool(retrieval_queries) is should_search
    if should_search:
        assert retrieval_queries == [query]
        assert response.json()["retrieval_route"] != "no_retrieval"
    else:
        assert response.json()["retrieval_route"] == "no_retrieval"
        assert response.json()["consulted_sources"] == []


def _document(client, title="RAG Notes"):
    response = _create_document(
        client,
        json={
            "title": title,
            "content": "RAG uses retrieval augmented generation and source evidence.",
        },
    )
    assert response.status_code == 201, response.text
    assert client.post(f"/documents/{response.json()['id']}/ingest").status_code == 200
    return response.json()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "priority,expected_route",
    [
        ("always", "retrieval_optional"),
        ("automatic", "no_retrieval"),
        ("on_request", "no_retrieval"),
    ],
)
def test_real_graph_preference_controls_search_not_just_source_gate(
    monkeypatch, streaming, priority, expected_route
):
    client = _client(monkeypatch, build_graph())
    _signup_login(client, "preference-real-graph@example.com")
    document = _document(client)
    _patch(client, document_retrieval_priority=priority)
    cid = client.post("/conversations", json={"title": "Source preference"}).json()["id"]
    response = client.post(
        f"/conversations/{cid}/runs" + ("/stream" if streaming else ""),
        json={"message": "What is RAG?"},
    )
    assert response.status_code == 200, response.text
    data = (
        next(item["data"] for item in _parse_sse(response.text) if item["event"] == "run_completed")
        if streaming
        else response.json()
    )
    assert data["retrieval_route"] == expected_route
    if priority == "always":
        assert {item["document_id"] for item in data["consulted_sources"]} == {document["id"]}
    else:
        assert data["consulted_sources"] == []


def test_always_still_honors_authorized_selected_source_scope(monkeypatch):
    client = _client(monkeypatch, build_graph())
    _signup_login(client, "preference-scope-owner@example.com")
    selected = _document(client, "Selected RAG")
    _document(client, "Other RAG")
    foreign = _client(monkeypatch)
    _signup_login(foreign, "preference-foreign-owner@example.com")
    secret = _document(foreign, "Private RAG")
    _patch(client, document_retrieval_priority="always")
    cid = client.post("/conversations", json={"title": "Scope"}).json()["id"]
    response = client.post(
        f"/conversations/{cid}/runs",
        json={
            "message": "What is RAG?",
            "knowledge_base_selection": {
                "mode": "selected",
                "knowledge_base_ids": [selected["knowledge_base_id"]],
            },
        },
    )
    assert response.status_code == 200, response.text
    assert {item["document_id"] for item in response.json()["consulted_sources"]} == {
        selected["id"]
    }
    denied = client.post(
        f"/conversations/{cid}/runs",
        json={
            "message": "What is RAG?",
            "knowledge_base_selection": {
                "mode": "selected",
                "knowledge_base_ids": [secret["knowledge_base_id"]],
            },
        },
    )
    assert denied.status_code == 404


@pytest.mark.parametrize("streaming", [False, True])
def test_new_runs_and_replays_pin_current_preference(monkeypatch, streaming):
    graph = ModelStreamingSpyGraph() if streaming else ModelSpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "preference-replay@example.com")
    _patch(client, document_retrieval_priority="always")
    cid = client.post("/conversations", json={"title": "Replay"}).json()["id"]
    suffix = "/stream" if streaming else ""
    response = client.post(f"/conversations/{cid}/runs{suffix}", json={"message": "Hello"})
    assert response.status_code == 200
    original = (
        next(item["data"] for item in _parse_sse(response.text) if item["event"] == "run_completed")
        if streaming
        else response.json()
    )
    assert graph.contexts[-1]["document_retrieval_priority"] == "always"
    _patch(client, document_retrieval_priority="on_request")
    sessions = get_database_session()
    db = next(sessions)
    try:
        assert db.get(AgentRunModel, original["run_id"]).document_retrieval_priority == "always"
    finally:
        sessions.close()
    mid = _assistant_message_id(original["run_id"])
    response = client.post(f"/conversations/{cid}/messages/{mid}/replay{suffix}", json={})
    assert response.status_code == 200, response.text
    assert graph.contexts[-1]["document_retrieval_priority"] == "on_request"


class ContextRecorder:
    def __init__(self, graph):
        self.graph = graph
        self.contexts = []

    def stream(self, input, **kwargs):
        self.contexts.append(kwargs["context"])
        yield from self.graph.stream(input, **kwargs)

    def get_state(self, *args, **kwargs):
        return self.graph.get_state(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.graph, name)


def test_durable_admission_keeps_preference_when_user_changes_it_before_worker(durable):
    client, settings, _, cid, _ = durable
    _patch(client, document_retrieval_priority="always")
    admitted, body = admit(client, cid)
    assert admitted.status_code == 202
    _patch(client, document_retrieval_priority="on_request")
    assert client.post(f"/conversations/{cid}/runs/async", json=body).json() == admitted.json()
    graph = ModelStreamingSpyGraph()
    assert process_once(settings, graph=graph)
    assert graph.contexts[-1]["document_retrieval_priority"] == "always"
    with _sessionmaker_for_url(settings.database_url)() as db:
        assert (
            db.get(AgentRunModel, admitted.json()["run_id"]).document_retrieval_priority == "always"
        )


def test_durable_resume_keeps_admitted_preference(durable):
    client, settings, _, cid, _ = durable
    _patch(client, document_retrieval_priority="on_request")
    graph, rid = waiting_run(durable)
    pending = client.get(f"/conversations/{cid}/runs/{rid}/output").json()
    assert pending["status"] == "waiting_for_input"
    _patch(client, document_retrieval_priority="always")
    response = client.post(
        f"/conversations/{cid}/runs/{rid}/resume/async",
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
    assert response.status_code == 202, response.text
    recorder = ContextRecorder(graph)
    assert process_once(settings, graph=recorder)
    assert recorder.contexts[-1]["document_retrieval_priority"] == "on_request"
    assert client.get(f"/conversations/{cid}/runs/{rid}/output").json()["status"] == "completed"


@pytest.mark.parametrize("streaming", [False, True])
def test_legacy_resume_keeps_admitted_preference(monkeypatch, streaming):
    graph = ContextRecorder(
        build_graph(
            checkpointer=InMemorySaver(serde=checkpoint_serializer()),
            document_selection_hitl_enabled=True,
        )
    )
    client = _client(monkeypatch, graph)
    _signup_login(client, "preference-resume@example.com")
    _patch(client, document_retrieval_priority="on_request")
    kb = _create_knowledge_base(client)
    for title in ("Alpha Source", "Beta Source"):
        doc = _create_document(
            client,
            json={
                "title": title,
                "content": f"{title} authorized evidence",
                "knowledge_base_id": kb,
            },
        )
        assert doc.status_code == 201
        assert client.post(f"/documents/{doc.json()['id']}/ingest").status_code == 200
    cid = client.post("/conversations", json={"title": "Resume"}).json()["id"]
    pending = client.post(
        f"/conversations/{cid}/runs",
        json={
            "message": "Summarize this document",
            "knowledge_base_selection": {"mode": "selected", "knowledge_base_ids": [kb]},
        },
    )
    assert pending.status_code == 202, pending.text
    data = pending.json()
    _patch(client, document_retrieval_priority="always")
    response = client.post(
        f"/conversations/{cid}/runs/{data['run_id']}/resume" + ("/stream" if streaming else ""),
        json={
            "schema_version": 2,
            "interaction_id": data["interaction"]["interaction_id"],
            "type": "document_selection",
            "kind": "refine",
            "text": "Alpha Source",
        },
    )
    assert response.status_code == 200, response.text
    assert graph.contexts[-1]["document_retrieval_priority"] == "on_request"
