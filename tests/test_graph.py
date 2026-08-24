"""LangGraph integration tests for the assistant graph path."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from my_agents.agents.general_assistant.graph import build_graph
from my_agents.agents.general_assistant.retrieval_gate import RetrievalSourceDecision
from my_agents.agents.rag_agent import RagAgentRetrievalResult
from my_agents.knowledge.retrieval import AuthorizedDocumentOption
from my_agents.knowledge.routing import RetrievalRoutingDecision
from my_agents.memory.runtime import MemoryRuntimeItem
from my_agents.persistence.langgraph import checkpoint_serializer

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
    assert result["rag_retrieval_snapshot"]["decision"]["route"] == "no_retrieval"
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
    assert result["rag_retrieval_snapshot"]["decision"]["route"] == "no_retrieval"


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


def test_checkpointed_graph_interrupts_and_resumes_document_selection() -> None:
    graph = build_graph(
        checkpointer=InMemorySaver(serde=checkpoint_serializer()),
        document_selection_hitl_enabled=True,
    )
    rag_runtime = ClarifyingRagRuntime()
    state = {
        **graph_state("Summarize this document", user_id="user-a"),
        "run_id": "run-hitl",
    }
    context = graph_runtime_context(
        user_id="user-a",
        rag_runtime=rag_runtime,
        retrieval_source_decider=FakeRetrievalSourceDecider(source="knowledge_base"),
    )
    config = {"configurable": {"thread_id": "run-hitl"}}

    interrupted = graph.invoke(state, config=config, context=context)

    assert interrupted["__interrupt__"][0].value["schema_version"] == 1
    assert interrupted["__interrupt__"][0].value["type"] == "document_selection"
    assert interrupted["__interrupt__"][0].value["options"][0]["document_id"] == "doc-1"
    assert "rag_retrieval_result" not in graph.get_state(config).values

    resumed = graph.invoke(
        Command(resume={"document_id": "doc-1"}),
        config=config,
        context=context,
    )

    assert resumed["selected_document_id"] == "doc-1"
    assert rag_runtime.selected_document_ids == ["doc-1"]
    assert resumed["rag_retrieval_snapshot"]["insufficient_evidence"] is True


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


class ClarifyingRagRuntime:
    def __init__(self) -> None:
        self.selected_document_ids: list[str] = []

    def retrieve_context(self, **kwargs):  # noqa: ANN003, ANN201
        selection_context = kwargs["selection_context"]
        selected_document_id = kwargs.get("selected_document_id")
        if selected_document_id is not None:
            self.selected_document_ids.append(selected_document_id)
            return RagAgentRetrievalResult(
                decision=RetrievalRoutingDecision(
                    route="retrieval_required",
                    reason="selected in test",
                    rewritten_query=kwargs["message"],
                    document_scope="user_documents",
                ),
                answer_mode="general_knowledge",
                retrieved_chunks=[],
                retrieval_latency_ms=0.0,
                knowledge_base_selection=selection_context,
                insufficient_evidence=True,
            )
        return RagAgentRetrievalResult(
            decision=RetrievalRoutingDecision(
                route="clarification_required",
                reason="ambiguous in test",
                rewritten_query=kwargs["message"],
                document_scope="unknown",
            ),
            answer_mode="general_knowledge",
            retrieved_chunks=[],
            retrieval_latency_ms=0.0,
            knowledge_base_selection=selection_context,
        )

    def document_options(self, **kwargs):  # noqa: ANN003, ANN201
        return (
            [
                AuthorizedDocumentOption(
                    document_id="doc-1",
                    title="Test document",
                    source_filename="test.pdf",
                    knowledge_base_id="kb-1",
                    knowledge_base_name="Test KB",
                )
            ],
            1,
        )


def invoke_graph_messages(message: str):
    from .conftest import messages_from_payload

    return messages_from_payload(message)
