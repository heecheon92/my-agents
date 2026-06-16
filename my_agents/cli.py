"""Interactive terminal entrypoint for the general assistant graph."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any, Protocol

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from my_agents.agents.general_assistant.graph import build_legacy_chat_graph

_EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit", "q"}


class GraphRunner(Protocol):
    """Minimal graph protocol used by the terminal chat loop."""

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002 - matches LangGraph API name
        """Stream graph events for one user turn."""
        ...


def main() -> None:
    """Run a small REPL that streams the general assistant graph response."""
    run_chat_loop(
        input_func=input,
        print_func=print,
        write_func=_stdout_write,
    )


def run_chat_loop(
    *,
    input_func: Callable[[str], str],
    print_func: Callable[[str], None],
    write_func: Callable[[str], None] | None = None,
    graph_factory: Callable[[], GraphRunner] = build_legacy_chat_graph,
) -> None:
    """Run an interactive chat loop with in-process message history.

    The loop uses LangGraph streaming so OpenAI-backed replies can appear token by token
    instead of waiting for the full response. Deterministic mode still works through the
    same path and prints the final graph update when no LLM tokens are emitted.
    """
    write = write_func or print_func
    graph = graph_factory()
    messages: list[AnyMessage] = []

    print_func("my-agents terminal chat")
    print_func("Type /exit to quit. History is kept only for this terminal session.")

    while True:
        try:
            user_input = input_func("You: ").strip()
        except EOFError, KeyboardInterrupt:
            print_func("\nGoodbye.")
            return

        if not user_input:
            continue
        if user_input.casefold() in _EXIT_COMMANDS:
            print_func("Goodbye.")
            return

        messages.append(HumanMessage(content=user_input))
        write("Assistant: ")
        reply = stream_graph_reply(graph=graph, messages=messages, write_func=write)
        messages.append(AIMessage(content=reply))


def stream_graph_reply(
    *,
    graph: GraphRunner,
    messages: list[AnyMessage],
    write_func: Callable[[str], None],
) -> str:
    """Stream one graph turn to the terminal and return the final reply text."""
    streamed_parts: list[str] = []
    final_reply = ""

    for event in graph.stream(
        {"messages": messages, "debug_empty_openai_response": True},
        stream_mode=["messages", "updates"],
        version="v2",
    ):
        event_type = event.get("type") if isinstance(event, dict) else None
        event_data = event.get("data") if isinstance(event, dict) else None

        if event_type == "messages":
            message_chunk, _metadata = event_data
            text = _message_chunk_text(message_chunk)
            if text:
                streamed_parts.append(text)
                write_func(text)
            continue

        if event_type == "updates" and isinstance(event_data, dict):
            reply = _reply_from_update(event_data)
            if reply:
                final_reply = reply

    if streamed_parts:
        write_func("\n")
        return final_reply or "".join(streamed_parts).strip()

    if final_reply:
        write_func(f"{final_reply}\n")
        return final_reply

    write_func("[No reply generated.]\n")
    return ""


def _reply_from_update(update: dict[str, Any]) -> str:
    """Extract a reply from a LangGraph updates-mode event."""
    for node_update in update.values():
        if isinstance(node_update, dict) and isinstance(node_update.get("reply"), str):
            return node_update["reply"]
    return ""


def _message_chunk_text(message_chunk: Any) -> str:
    """Extract displayable text from a LangChain streaming message chunk."""
    text = getattr(message_chunk, "text", "")
    if isinstance(text, str) and text:
        return str(text)

    content = getattr(message_chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "".join(parts)
    return ""


def _stdout_write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
