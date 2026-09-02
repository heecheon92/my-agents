"""Tests for LangGraph stream adaptation into conversation SSE events."""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from my_agents.agents.general_assistant.responders import _stream_and_aggregate_response
from my_agents.api.conversations.graph_streaming import stream_graph_items


class _TextChunk:
    def __init__(self, content: object) -> None:
        self.content = content


class _GateThenResponderGraph:
    def invoke(self, input: dict) -> dict:  # noqa: A002, ARG002 - mirrors LangGraph API
        raise AssertionError("streaming adapter should prefer graph.stream")

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002, ARG002 - mirrors LangGraph API
        yield {
            "type": "messages",
            "data": (
                _TextChunk('{"source":"bypass"'),
                {"langgraph_node": "decide_retrieval_source"},
            ),
        }
        yield {
            "type": "updates",
            "data": {
                "skip_rag_context": {
                    "retrieval_route": "no_retrieval",
                    "answer_mode": "general_knowledge",
                    "document_scope": "unknown",
                    "rag_halt_before_response": False,
                }
            },
        }
        yield {
            "type": "messages",
            "data": (_TextChunk("visible "), {"langgraph_node": "respond_general"}),
        }
        yield {
            "type": "messages",
            "data": (_TextChunk("answer"), {"langgraph_node": "respond_general"}),
        }
        yield {
            "type": "updates",
            "data": {
                "respond_general": {
                    "reply": "visible answer",
                }
            },
        }


def test_stream_graph_items_ignores_non_responder_llm_chunks() -> None:
    items = list(
        stream_graph_items(
            graph_runner=_GateThenResponderGraph(),
            graph_input={"messages": []},
        )
    )

    assert [item.delta for item in items if item.kind == "delta"] == [
        "visible ",
        "answer",
    ]
    assert items[-1].kind == "result"
    assert items[-1].result is not None
    assert items[-1].result["reply"] == "visible answer"
    assert '{"source":"bypass"' not in "".join(item.delta for item in items if item.kind == "delta")


class _ReasoningSummaryGraph:
    def stream(self, input: dict, **kwargs: Any):  # noqa: A002, ARG002
        yield {
            "type": "updates",
            "data": {
                "decide_retrieval_source": {
                    "retrieval_planning_summary": "I chose focused document search."
                }
            },
        }
        yield {
            "type": "messages",
            "data": (
                _TextChunk(
                    [
                        {
                            "type": "reasoning",
                            "summary": [
                                {
                                    "type": "summary_text",
                                    "text": "I organized the evidence by topic. ",
                                }
                            ],
                        }
                    ]
                ),
                {"langgraph_node": "respond_general"},
            ),
        }
        yield {
            "type": "updates",
            "data": {
                "respond_general": {
                    "reply": "Final answer",
                    "answer_synthesis_summary": "I organized the evidence by topic.",
                }
            },
        }


def test_stream_graph_items_separates_summary_deltas_from_answer_text() -> None:
    items = list(
        stream_graph_items(
            graph_runner=_ReasoningSummaryGraph(),
            graph_input={"messages": []},
        )
    )

    summaries = [item for item in items if item.kind == "reasoning_delta"]
    assert [(item.stage, item.delta) for item in summaries] == [
        ("retrieval_planning", "I chose focused document search."),
        ("answer_synthesis", "I organized the evidence by topic. "),
    ]
    assert [item.delta for item in items if item.kind == "delta"] == []


class _StreamingProviderState(TypedDict, total=False):
    messages: list[BaseMessage]
    reply: str


def test_real_langgraph_forwards_provider_stream_chunks_before_final_update() -> None:
    model = FakeListChatModel(responses=["token stream"])

    def respond(state: _StreamingProviderState) -> dict[str, str]:
        response = _stream_and_aggregate_response(model, state["messages"])
        return {"reply": response.text}

    graph = (
        StateGraph(_StreamingProviderState)
        .add_node("respond_general", respond)
        .add_edge(START, "respond_general")
        .add_edge("respond_general", END)
        .compile()
    )

    items = list(
        stream_graph_items(
            graph_runner=graph,
            graph_input={"messages": [HumanMessage(content="Stream this response")]},
        )
    )

    deltas = [item.delta for item in items if item.kind == "delta"]
    assert deltas == list("token stream")
    assert items[-1].kind == "result"
    assert items[-1].result is not None
    assert items[-1].result["reply"] == "token stream"
