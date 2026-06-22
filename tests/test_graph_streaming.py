"""Tests for LangGraph stream adaptation into conversation SSE events."""

from __future__ import annotations

from typing import Any

from my_agents.api.conversations.graph_streaming import stream_graph_items


class _TextChunk:
    def __init__(self, content: str) -> None:
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
