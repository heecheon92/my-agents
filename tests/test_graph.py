"""LangGraph integration tests for the assistant graph path."""

from __future__ import annotations

import pytest

from my_agents.memory.runtime import MemoryRuntimeItem

from .conftest import (
    REPRESENTATIVE_PROMPTS,
    assert_chat_response_shape,
    assert_no_delegation_claims,
    get_compiled_graph,
    graph_runtime_context,
    graph_state,
    invoke_graph,
)


def test_graph_compiles_to_real_invokable_langgraph_path() -> None:
    graph = get_compiled_graph()

    assert callable(graph.invoke)
    result = graph.invoke(
        graph_state("Hello, what can you do?"),
        context=graph_runtime_context(),
    )

    assert_chat_response_shape(result, expected_label="general_assistant")


def test_product_graph_requires_rag_runtime_context() -> None:
    graph = get_compiled_graph()

    with pytest.raises(RuntimeError, match="requires RAG Agent runtime context"):
        graph.invoke(graph_state("Hello, what can you do?"))


def test_legacy_chat_graph_omits_rag_runtime_requirement() -> None:
    from my_agents.agents.general_assistant.graph import build_legacy_chat_graph

    result = build_legacy_chat_graph().invoke({"messages": invoke_graph_messages("Hello")})

    assert_chat_response_shape(result, expected_label="general_assistant")
    assert "rag_retrieval_result" not in result


@pytest.mark.parametrize("expected_label,prompt", REPRESENTATIVE_PROMPTS.items())
def test_graph_invocation_reaches_response_output_for_every_route_label(
    expected_label: str, prompt: str
) -> None:
    result = invoke_graph(prompt)

    assert_chat_response_shape(result, expected_label=expected_label)


def test_graph_accepts_history_context_without_claiming_persistent_memory() -> None:
    result = invoke_graph(
        "Continue with the next project planning step",
        history=[
            {"role": "user", "content": "I am building a FastAPI LangGraph backend."},
            {"role": "assistant", "content": "We identified a classify-only router milestone."},
        ],
    )

    assert_chat_response_shape(result)
    assert "persistent memory" not in str(result).lower()
    assert_no_delegation_claims(result)


def test_graph_retrieves_memory_from_runtime_context() -> None:
    graph = get_compiled_graph()
    memory_runtime = FakeMemoryRuntime(
        [
            MemoryRuntimeItem(
                id="memory-1",
                key="stable-preference-memory-1",
                category="stable_preference",
                content="User prefers concise answers",
                provenance_type="explicit_user",
            )
        ]
    )

    result = graph.invoke(
        graph_state("Actually I no longer prefer concise answers", user_id="user-a"),
        context=graph_runtime_context(user_id="user-a", memory_runtime=memory_runtime),
    )

    assert memory_runtime.queries == ["Actually I no longer prefer concise answers"]
    assert result["memory_context"][0]["content"] == "User prefers concise answers"
    assert result["source_conflicts"][0]["primary"] == "conversation"
    assert result["source_conflicts"][0]["secondary"] == "memory"


class FakeMemoryRuntime:
    def __init__(self, items: list[MemoryRuntimeItem]) -> None:
        self._items = items
        self.queries: list[str] = []

    def search(
        self,
        *,
        user_id: str,  # noqa: ARG002 - fake keeps the query assertion focused.
        query: str,
        categories: list[object] | None = None,  # noqa: ARG002
        limit: int = 8,  # noqa: ARG002
    ) -> list[MemoryRuntimeItem]:
        self.queries.append(query)
        return self._items


def invoke_graph_messages(message: str):
    from .conftest import messages_from_payload

    return messages_from_payload(message)
