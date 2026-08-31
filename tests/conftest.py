"""Shared test helpers for the v0 assistant/router contract."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from my_agents.agents.rag_agent import RagAgentRetrievalResult
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.routing import RetrievalRoutingDecision

ALLOWED_ROUTE_LABELS = {
    "general_assistant",
    "research_helper",
}

REPRESENTATIVE_PROMPTS = {
    "research_helper": "Find sources about FastAPI testing",
    "general_assistant": "Hello, what can you do?",
}

FORBIDDEN_DELEGATION_PHRASES = (
    "delegated to",
    "delegating to",
    "routed to agent",
    "handled by agent",
    "specialized agent executed",
    "specialized agent completed",
    "specialist agent executed",
    "specialist agent completed",
    "agent executed",
    "agent completed",
)


@pytest.fixture(autouse=True)
def deterministic_runtime_env(monkeypatch: pytest.MonkeyPatch):
    """Keep tests offline and isolated even when a developer has a local `.env` file."""
    monkeypatch.setenv("MY_AGENTS_ENV_FILE", "")
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", "true")
    _clear_runtime_caches()
    yield
    _clear_runtime_caches()


def load_app():
    """Import the ASGI app from main, matching the documented run target."""
    main = importlib.import_module("main")
    app = getattr(main, "app", None)
    if app is None:
        pytest.fail("main.py must expose FastAPI ASGI app as `app = create_app()`")
    return app


def as_dict(value: Any) -> dict[str, Any]:
    """Normalize Pydantic models, dataclasses, and mappings to plain dicts."""
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    pytest.fail(f"Expected mapping/model-like value, got {type(value)!r}: {value!r}")


def assert_route_decision(decision: Any, expected_label: str | None = None) -> dict[str, Any]:
    data = as_dict(decision)
    assert data.get("label") in ALLOWED_ROUTE_LABELS
    if expected_label is not None:
        assert data["label"] == expected_label
    assert isinstance(data.get("explanation"), str)
    assert data["explanation"].strip()
    assert_no_delegation_claims(data)
    return data


def assert_chat_response_shape(
    payload: Mapping[str, Any],
    expected_label: str | None = None,
) -> None:
    assert isinstance(payload.get("reply"), str)
    assert payload["reply"].strip()
    assert payload.get("route") is not None
    assert_route_decision(payload["route"], expected_label=expected_label)
    assert payload.get("handled_by") == "personal_assistant_graph"
    assert_no_delegation_claims(payload)


def assert_no_delegation_claims(value: Any) -> None:
    text = str(value).lower().replace("_", " ")
    for phrase in FORBIDDEN_DELEGATION_PHRASES:
        assert phrase not in text


def latest_auth_email_token(email: str, purpose: str) -> str:
    """Return the newest local auth email token for a recipient and purpose."""
    from my_agents.auth.email import get_local_auth_email_outbox

    normalized = email.strip().casefold()
    matches = [
        message
        for message in get_local_auth_email_outbox().messages()
        if message.recipient_email == normalized and message.purpose == purpose
    ]
    assert matches, f"expected local auth email for {normalized} with purpose {purpose}"
    return matches[-1].token


def verify_latest_auth_email(client: Any, email: str) -> dict[str, Any]:
    """Verify the newest local signup email for a test client."""
    token = latest_auth_email_token(email, "email_verification")
    response = client.post("/auth/verify-email", json={"token": token})
    assert response.status_code == 200
    return response.json()


def get_classifier() -> Callable[[str], Any]:
    module = importlib.import_module("my_agents.agents.general_assistant.classifier")
    for name in ("classify_message", "classify_request", "classify_route", "classify"):
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    pytest.fail(
        "my_agents.agents.general_assistant.classifier must expose a callable classifier "
        "(preferred: classify_message(message: str) -> RouteDecision)"
    )


def get_compiled_graph():
    module = importlib.import_module("my_agents.agents.general_assistant.graph")
    for name in (
        "build_assistant_graph",
        "create_assistant_graph",
        "compile_assistant_graph",
        "build_graph",
    ):
        factory = getattr(module, name, None)
        if callable(factory):
            graph = factory()
            break
    else:
        graph = getattr(module, "assistant_graph", None) or getattr(module, "graph", None)
    if graph is None:
        pytest.fail(
            "my_agents.agents.general_assistant.graph must expose a compiled "
            "assistant graph or graph factory"
        )
    if not hasattr(graph, "invoke") and hasattr(graph, "compile"):
        graph = graph.compile()
    assert hasattr(graph, "invoke"), "assistant graph must compile to an invokable LangGraph object"
    return graph


def invoke_graph(message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    graph = get_compiled_graph()
    state = graph_state(message, history)
    return as_dict(graph.invoke(state, context=graph_runtime_context()))


def graph_state(
    message: str,
    history: list[dict[str, str]] | None = None,
    *,
    user_id: str = "test-user",
    conversation_id: str = "test-conversation",
) -> dict[str, Any]:
    return {
        "messages": messages_from_payload(message, history),
        "principal_id": user_id,
        "conversation_id": conversation_id,
    }


def graph_runtime_context(
    *,
    user_id: str = "test-user",
    rag_runtime: Any | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    context = {
        "user_id": user_id,
        "rag_runtime": rag_runtime or FakeRagRuntime(),
        "knowledge_base_selection": KnowledgeBaseSelectionContext(
            mode="all",
            knowledge_base_ids=(),
            resolved_count=0,
        ),
    }
    context.update(overrides)
    return context


class FakeRagRuntime:
    """No-document RAG Agent runtime for graph unit tests."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve_context(self, **kwargs: Any) -> RagAgentRetrievalResult:
        self.queries.append(str(kwargs["message"]))
        return RagAgentRetrievalResult(
            decision=RetrievalRoutingDecision(
                route="no_retrieval",
                reason="unit test RAG runtime has no documents",
                rewritten_query=str(kwargs["message"]),
                document_scope="none",
            ),
            answer_mode="general_knowledge",
            retrieved_chunks=[],
            retrieval_latency_ms=0.0,
            knowledge_base_selection=kwargs["selection_context"],
        )


def messages_from_payload(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> list[AnyMessage]:
    messages: list[AnyMessage] = []
    for item in history or []:
        if item["role"] == "assistant":
            messages.append(AIMessage(content=item["content"]))
        else:
            messages.append(HumanMessage(content=item["content"]))
    messages.append(HumanMessage(content=message))
    return messages


def _clear_runtime_caches() -> None:
    for module_name, cached_names in {
        "my_agents.settings": ("get_settings",),
        "my_agents.agents.general_assistant.responders": ("get_response_provider",),
        "my_agents.agents.general_assistant.retrieval_gate": ("get_retrieval_source_decider",),
        "my_agents.agents.rag_agent.tool_selection": ("get_rag_retrieval_tool_decider",),
        "my_agents.persistence.database": ("reset_database_caches",),
        "my_agents.knowledge.embeddings": ("get_embedding_provider",),
        "my_agents.auth.abuse": ("reset_auth_abuse_protector",),
        "my_agents.auth.email": ("reset_local_auth_email_outbox",),
    }.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name in cached_names:
            cached = getattr(module, name, None)
            cache_clear = getattr(cached, "cache_clear", None)
            if callable(cache_clear):
                cache_clear()
            elif callable(cached):
                cached()
