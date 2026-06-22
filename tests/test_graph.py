"""LangGraph integration tests for the assistant graph path."""

from __future__ import annotations

import pytest

from my_agents.agents.general_assistant.retrieval_gate import RetrievalSourceDecision
from my_agents.memory.runtime import MemoryRuntimeItem

from .conftest import (
    REPRESENTATIVE_PROMPTS,
    FakeRagRuntime,
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


def test_graph_bypasses_rag_when_user_excludes_saved_docs() -> None:
    graph = get_compiled_graph()
    rag_runtime = FakeRagRuntime()

    result = graph.invoke(
        graph_state("Don't use saved docs. What is RAG?", user_id="user-a"),
        context=graph_runtime_context(user_id="user-a", rag_runtime=rag_runtime),
    )

    assert rag_runtime.queries == []
    assert result["retrieval_source_decision"].source == "bypass"
    assert result["rag_retrieval_result"].decision.route == "no_retrieval"
    assert result["retrieved_context"] == []


def test_graph_enters_rag_when_source_gate_selects_knowledge_base() -> None:
    graph = get_compiled_graph()
    rag_runtime = FakeRagRuntime()

    result = graph.invoke(
        graph_state("Summarize my uploaded document", user_id="user-a"),
        context=graph_runtime_context(user_id="user-a", rag_runtime=rag_runtime),
    )

    assert rag_runtime.queries == ["Summarize my uploaded document"]
    assert result["retrieval_source_decision"].source == "knowledge_base"
    assert result["rag_retrieval_result"].decision.route == "no_retrieval"


def test_graph_accepts_runtime_source_decider_for_multilingual_gate() -> None:
    graph = get_compiled_graph()
    rag_runtime = FakeRagRuntime()
    source_decider = FakeRetrievalSourceDecider(source="bypass")

    result = graph.invoke(
        graph_state("웹에서 찾아보고 저장된 문서는 쓰지 마", user_id="user-a"),
        context=graph_runtime_context(
            user_id="user-a",
            rag_runtime=rag_runtime,
            retrieval_source_decider=source_decider,
        ),
    )

    assert source_decider.messages == ["웹에서 찾아보고 저장된 문서는 쓰지 마"]
    assert rag_runtime.queries == []
    assert result["retrieval_source_decision"].source == "bypass"


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


class FakeRetrievalSourceDecider:
    def __init__(self, *, source: str) -> None:
        self._source = source
        self.messages: list[str] = []

    def decide(self, *, messages, selection_context):  # noqa: ANN001
        _ = selection_context
        self.messages.append(str(messages[-1].content))
        return RetrievalSourceDecision(
            source=self._source,  # type: ignore[arg-type]
            reason="fake source decider",
        )


def invoke_graph_messages(message: str):
    from .conftest import messages_from_payload

    return messages_from_payload(message)
