"""Agent event observability and deterministic eval fixture tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from my_agents.agent_runtime.evals import (
    evaluate_event_latency_budget,
    evaluate_event_redaction,
    evaluate_permission_leakage,
)
from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email
from .rag_spy_helpers import rag_update_for_spy


class ObservabilitySpyGraph:
    """Graph spy that avoids echoing private document text in base replies."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict, **kwargs: Any) -> dict:  # noqa: A002 - matches LangGraph API
        rag_update = rag_update_for_spy(input, kwargs)
        self.calls.append({**input, **rag_update})
        return {
            **rag_update,
            "reply": "safe graph response",
            "route": RouteDecision(label="general_assistant", explanation="spy route"),
        }


def _client(monkeypatch, graph: ObservabilitySpyGraph | None = None) -> TestClient:  # noqa: ANN001
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


def _create_knowledge_base(client: TestClient, name: str = "Test KB") -> str:
    response = client.post("/knowledge-bases", json={"name": name, "scope": "personal"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_document(client: TestClient, *, json: dict):  # noqa: ANN201
    payload = dict(json)
    payload.setdefault("knowledge_base_id", _create_knowledge_base(client))
    return client.post("/documents", json=payload)


def test_chat_run_events_are_ordered_structured_and_redacted(monkeypatch) -> None:  # noqa: ANN001
    graph = ObservabilitySpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "events@example.com")
    kb_id = _create_knowledge_base(client, "Events KB")
    private_phrase = "Orion Agent Trace"
    document = _create_document(
        client,
        json={
            "title": "Trace Notes",
            "content": f"{private_phrase} uses LangGraph events.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert client.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Events"}).json()["id"]

    run = client.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "How does Orion tracing work?"},
    )
    assert run.status_code == 200
    run_payload = run.json()

    events = client.get(f"/conversations/{conversation_id}/runs/{run_payload['run_id']}/events")
    assert events.status_code == 200
    event_payload = events.json()
    assert [event["sequence"] for event in event_payload] == [1, 2, 3, 4, 5]
    assert [event["event_type"] for event in event_payload] == [
        "run_started",
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "answer_composed",
    ]
    payloads = [event["payload"] for event in event_payload]
    assert payloads[2]["authorized_context_count"] == 1
    assert (
        sum(
            payloads[2][key]
            for key in (
                "semantic_vector_count",
                "keyword_match_count",
                "document_metadata_count",
                "document_metadata_profile_count",
                "graph_expansion_count",
                "fallback_count",
            )
        )
        == 1
    )
    assert payloads[3]["route_label"] == "general_assistant"
    assert payloads[4]["citation_count"] == 0
    assert private_phrase not in str(payloads)
    assert "How does Orion" not in str(payloads)

    assert run_payload["citations"] == []
    consulted = [source["snippet"] for source in run_payload["consulted_sources"]]
    assert any(private_phrase in snippet for snippet in consulted)
    assert private_phrase not in run_payload["reply"]
    assert evaluate_event_redaction(
        event_payloads=payloads,
        forbidden_terms=[private_phrase, "How does Orion"],
    ).passed
    assert evaluate_event_latency_budget(event_payloads=payloads, max_latency_ms=250).passed
    assert graph.calls[-1]["retrieved_chunk_ids"]


def test_eval_fixture_detects_permission_leakage(monkeypatch) -> None:  # noqa: ANN001
    graph = ObservabilitySpyGraph()
    owner = _client(monkeypatch, graph)
    outsider = _client(monkeypatch, graph)
    _signup_login(owner, "eval-owner@example.com")
    _signup_login(outsider, "eval-outsider@example.com")
    kb_id = _create_knowledge_base(owner, "Eval KB")
    forbidden = "Velvet Private Strategy"
    document = _create_document(
        owner,
        json={
            "title": "Private Eval",
            "content": f"{forbidden} belongs to the owner.",
            "knowledge_base_id": kb_id,
        },
    )
    assert document.status_code == 201
    assert owner.post(f"/documents/{document.json()['id']}/ingest").status_code == 200
    conversation_id = outsider.post("/conversations", json={"title": "No Leak"}).json()["id"]

    run = outsider.post(
        f"/conversations/{conversation_id}/runs",
        json={"message": "Tell me the Velvet strategy"},
    )

    assert run.status_code == 200
    payload = run.json()
    snippets = [source["snippet"] for source in payload["consulted_sources"] or []]
    leakage = evaluate_permission_leakage(
        reply=payload["reply"],
        citation_snippets=snippets,
        forbidden_terms=[forbidden],
    )
    assert leakage.passed
    assert payload["citations"] == []
