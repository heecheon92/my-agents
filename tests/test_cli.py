"""CLI tests for the terminal chat loop."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk

from my_agents.cli import run_chat_loop, stream_graph_reply


class FakeStreamingGraph:
    def stream(self, input: dict, **kwargs: Any):  # noqa: A002 - mirrors LangGraph API
        assert input["debug_empty_openai_response"] is True
        assert kwargs["stream_mode"] == ["messages", "updates"]
        assert kwargs["version"] == "v2"
        yield {"type": "messages", "data": (AIMessageChunk(content="Hel"), {"node": "test"})}
        yield {"type": "messages", "data": (AIMessageChunk(content="lo"), {"node": "test"})}
        yield {
            "type": "updates",
            "data": {"respond_general": {"reply": "Hello"}},
        }


def test_cli_chat_loop_handles_one_turn_and_exit() -> None:
    inputs = iter(["Help me study LangGraph", "/exit"])
    outputs: list[str] = []

    run_chat_loop(input_func=lambda _prompt: next(inputs), print_func=outputs.append)

    assert outputs[0] == "my-agents terminal chat"
    assert any("learning_coach" in output for output in outputs)
    assert outputs[-1] == "Goodbye."


def test_stream_graph_reply_writes_token_chunks_before_returning_final_reply() -> None:
    writes: list[str] = []

    reply = stream_graph_reply(
        graph=FakeStreamingGraph(),
        messages=[],
        write_func=writes.append,
    )

    assert writes == ["Hel", "lo", "\n"]
    assert reply == "Hello"
