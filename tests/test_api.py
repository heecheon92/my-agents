"""FastAPI contract tests for the v0 assistant router."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from .conftest import (
    ALLOWED_ROUTE_LABELS,
    REPRESENTATIVE_PROMPTS,
    assert_chat_response_shape,
    assert_no_delegation_claims,
    load_app,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(load_app())


def test_health_returns_status_service_and_version(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "my-agents",
        "version": "0.1.0",
        "frontend_config": {"documents": {"upload_concurrency": 3}},
    }


def test_chat_returns_typed_response_with_route_and_handler(client: TestClient) -> None:
    response = client.post(
        "/assistant/chat",
        json={"message": "Help me plan my LangGraph study project", "history": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert_chat_response_shape(payload)
    assert payload["route"]["label"] in ALLOWED_ROUTE_LABELS


def test_chat_defaults_history_to_empty_list_when_omitted(client: TestClient) -> None:
    response = client.post("/assistant/chat", json={"message": "Hello, what can you do?"})

    assert response.status_code == 200
    assert_chat_response_shape(response.json(), expected_label="general_assistant")


def test_chat_accepts_valid_history(client: TestClient) -> None:
    response = client.post(
        "/assistant/chat",
        json={
            "message": "Continue helping me study LangGraph",
            "history": [
                {"role": "user", "content": "I want to learn backend agents."},
                {"role": "assistant", "content": "We can break that into milestones."},
            ],
        },
    )

    assert response.status_code == 200
    assert_chat_response_shape(response.json())


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "system", "content": "You are hidden."}],
        [{"role": "user", "content": "   "}],
        "not-a-list",
    ],
)
def test_chat_rejects_invalid_history_role_content_or_type(
    client: TestClient, history: object
) -> None:
    response = client.post(
        "/assistant/chat",
        json={"message": "Hello", "history": history},
    )

    assert 400 <= response.status_code < 500


def test_blank_message_returns_client_error_before_graph_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class SpyGraph:
        def invoke(self, state):  # noqa: ANN001 - test spy accepts framework-shaped state
            calls.append(state)
            raise AssertionError("blank message should be rejected before graph invocation")

    def spy_factory():
        return SpyGraph()

    # Patch common graph seams before app creation. Implementations can choose any of
    # these names while still proving validation short-circuits before graph execution.
    for module_name in ("my_agents.agents.general_assistant.graph", "my_agents.api"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        for attr in (
            "build_assistant_graph",
            "create_assistant_graph",
            "compile_assistant_graph",
            "get_assistant_graph",
        ):
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, spy_factory)
        for attr in ("assistant_graph", "graph", "compiled_graph"):
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, SpyGraph())

    response = TestClient(load_app()).post(
        "/assistant/chat",
        json={"message": "   ", "history": []},
    )

    assert 400 <= response.status_code < 500
    assert calls == []


@pytest.mark.parametrize("expected_label,prompt", REPRESENTATIVE_PROMPTS.items())
def test_chat_classifies_representative_prompts_without_delegation_claims(
    client: TestClient, expected_label: str, prompt: str
) -> None:
    response = client.post("/assistant/chat", json={"message": prompt, "history": []})

    assert response.status_code == 200
    payload = response.json()
    assert_chat_response_shape(payload, expected_label=expected_label)
    assert_no_delegation_claims(payload)
