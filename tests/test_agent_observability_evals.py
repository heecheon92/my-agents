"""Agent event observability and deterministic eval fixture tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from my_agents.agent_runtime.evals import (
    evaluate_event_latency_budget,
    evaluate_event_redaction,
    evaluate_grounded_citations,
    evaluate_permission_leakage,
)
from my_agents.api import create_app
from my_agents.api.assistant import get_graph_runner
from my_agents.schemas import RouteDecision

from .conftest import verify_latest_auth_email


class ObservabilitySpyGraph:
    """Graph spy that avoids echoing private document text in base replies."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, input: dict) -> dict:  # noqa: A002 - matches LangGraph API
        self.calls.append(input)
        return {
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
    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    verify_latest_auth_email(client, email)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return signup.json()["user"]["id"]


def test_chat_run_events_are_ordered_structured_and_redacted(monkeypatch) -> None:  # noqa: ANN001
    graph = ObservabilitySpyGraph()
    client = _client(monkeypatch, graph)
    _signup_login(client, "events@example.com")
    private_phrase = "Orion Agent Trace"
    document = client.post(
        "/documents",
        json={"title": "Trace Notes", "content": f"{private_phrase} uses LangGraph events."},
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
    assert [event["sequence"] for event in event_payload] == [1, 2, 3, 4]
    assert [event["event_type"] for event in event_payload] == [
        "user_message_stored",
        "retrieval_completed",
        "graph_invoked",
        "answer_composed",
    ]
    payloads = [event["payload"] for event in event_payload]
    assert payloads[1]["authorized_context_count"] == 1
    assert payloads[1]["direct_count"] == 1
    assert payloads[2]["route_label"] == "general_assistant"
    assert payloads[3]["citation_count"] == 1
    assert private_phrase not in str(payloads)
    assert "How does Orion" not in str(payloads)

    citations = [citation["snippet"] for citation in run_payload["citations"]]
    grounded = evaluate_grounded_citations(
        reply=run_payload["reply"],
        citation_snippets=citations,
    )
    assert grounded.passed
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
    forbidden = "Velvet Private Strategy"
    document = owner.post(
        "/documents",
        json={"title": "Private Eval", "content": f"{forbidden} belongs to the owner."},
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
    snippets = [citation["snippet"] for citation in payload["citations"]]
    leakage = evaluate_permission_leakage(
        reply=payload["reply"],
        citation_snippets=snippets,
        forbidden_terms=[forbidden],
    )
    assert leakage.passed
    assert payload["citations"] == []
