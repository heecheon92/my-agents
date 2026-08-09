"""LangGraph streaming adapters for conversation SSE responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.api.assistant import GraphRunner
from my_agents.api.conversations.graph_invocation import invoke_graph_runner, stream_graph_runner

GraphStreamItemKind = Literal["delta", "update", "result"]
_ASSISTANT_RESPONSE_STREAM_NODES = frozenset({"respond_general", "respond_research"})


@dataclass(frozen=True)
class GraphStreamItem:
    """Internal item emitted while adapting graph stream events to SSE."""

    kind: GraphStreamItemKind
    delta: str = ""
    result: dict[str, Any] | None = None


def stream_graph_items(
    *,
    graph_runner: GraphRunner,
    graph_input: dict,
    graph_context: dict[str, object] | None = None,
):
    """Yield assistant text deltas plus one final graph result.

    The compiled LangGraph runner supports `.stream(...)` in local CLI usage. Tests and
    simple spies may only implement `.invoke(...)`, so this adapter falls back to invoke
    while still emitting deterministic `answer_delta` chunks before `run_completed`.
    """
    stream = getattr(graph_runner, "stream", None)
    if not callable(stream):
        yield GraphStreamItem(
            kind="result",
            result=invoke_graph_runner(
                graph_runner=graph_runner,
                graph_input=graph_input,
                graph_context=graph_context,
            ),
        )
        return

    streamed_parts: list[str] = []
    final_result: dict[str, Any] = {}
    emitted_stream_event = False
    for event in stream_graph_runner(
        stream=stream,
        graph_input=graph_input,
        graph_context=graph_context,
        stream_mode=["messages", "updates"],
        version="v2",
    ):
        emitted_stream_event = True
        event_type, event_data = stream_event_parts(event)
        if event_type == "messages":
            message_chunk, metadata = event_data
            if not should_emit_message_chunk(metadata):
                continue
            text = message_chunk_text(message_chunk)
            if text:
                streamed_parts.append(text)
                yield GraphStreamItem(kind="delta", delta=text)
            continue
        if event_type == "updates" and isinstance(event_data, dict):
            fields = result_fields_from_update(event_data)
            if fields:
                final_result.update(fields)
                yield GraphStreamItem(kind="update", result=fields)

    if "reply" not in final_result and streamed_parts:
        final_result["reply"] = "".join(streamed_parts).strip()
    if "route" not in final_result:
        final_result["route"] = classify_messages(graph_input.get("messages", []))
    if "reply" in final_result or "rag_retrieval_result" in final_result:
        yield GraphStreamItem(kind="result", result=final_result)
        return
    if not emitted_stream_event:
        yield GraphStreamItem(
            kind="result",
            result=invoke_graph_runner(
                graph_runner=graph_runner,
                graph_input=graph_input,
                graph_context=graph_context,
            ),
        )
        return
    raise RuntimeError("graph stream did not yield a reply")


def stream_event_parts(event: Any) -> tuple[str | None, Any]:
    if isinstance(event, dict):
        return event.get("type"), event.get("data")
    if isinstance(event, tuple) and len(event) == 2:
        return event
    return None, None


def should_emit_message_chunk(metadata: Any) -> bool:
    """Return whether a LangGraph message chunk is user-visible assistant text.

    `stream_mode="messages"` includes tokens from every chat model invoked inside the
    graph. Source-selection/routing nodes may also use an LLM, but their JSON/control
    tokens must not be forwarded as answer deltas. Older test doubles and lightweight
    graph spies often omit LangGraph metadata, so unknown metadata remains visible for
    backwards compatibility.
    """
    if not isinstance(metadata, Mapping):
        return True
    node_name = metadata.get("langgraph_node")
    if not isinstance(node_name, str):
        return True
    return node_name in _ASSISTANT_RESPONSE_STREAM_NODES


def result_fields_from_update(update: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for node_update in update.values():
        if not isinstance(node_update, dict):
            continue
        route = node_update.get("route")
        if route is not None:
            fields["route"] = route
        reply = node_update.get("reply")
        if isinstance(reply, str):
            fields["reply"] = reply
        for field_name in (
            "memory_context",
            "source_conflicts",
            "retrieved_chunk_ids",
            "retrieved_context",
            "document_artifacts",
        ):
            value = node_update.get(field_name)
            if isinstance(value, list):
                fields[field_name] = value
        for field_name in (
            "rag_retrieval_result",
            "rag_halt_before_response",
            "retrieval_route",
            "answer_mode",
            "document_scope",
            "document_workspace_expires_at",
        ):
            if field_name in node_update:
                fields[field_name] = node_update[field_name]
    return fields


def message_chunk_text(message_chunk: Any) -> str:
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


def fallback_answer_deltas(reply: str) -> list[str]:
    words = reply.split(" ")
    if len(words) <= 1:
        return [reply] if reply else []
    return [f"{word} " for word in words[:-1]] + [words[-1]]
