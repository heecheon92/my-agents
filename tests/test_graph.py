"""LangGraph integration tests for the assistant graph path."""

from __future__ import annotations

import pytest

from .conftest import (
    REPRESENTATIVE_PROMPTS,
    assert_chat_response_shape,
    assert_no_delegation_claims,
    get_compiled_graph,
    invoke_graph,
)


def test_graph_compiles_to_real_invokable_langgraph_path() -> None:
    graph = get_compiled_graph()

    assert callable(graph.invoke)
    result = graph.invoke({"message": "Hello, what can you do?", "history": []})

    assert_chat_response_shape(result, expected_label="general_assistant")


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
