"""LangGraph streaming adapters for conversation SSE responses."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.types import Command

from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.api.assistant import GraphRunner
from my_agents.api.conversations.graph_invocation import (
    invoke_graph_runner,
    invoke_graph_runner_resume_collecting_updates,
    stream_graph_runner,
)

GraphStreamItemKind = Literal["delta", "reasoning_delta", "update", "result"]
# Full-document replies are intentionally buffered so the deterministic partial-coverage
# disclosure is included before any user-visible answer delta is emitted.
_ASSISTANT_RESPONSE_STREAM_NODES = frozenset({"respond_general", "respond_research"})


@dataclass(frozen=True)
class GraphStreamItem:
    """Internal item emitted while adapting graph stream events to SSE."""

    kind: GraphStreamItemKind
    delta: str = ""
    stage: Literal["retrieval_planning", "answer_synthesis"] | None = None
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
    reasoning_stages_emitted: set[str] = set()
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
            summary_delta = message_chunk_reasoning_summary_delta(message_chunk)
            if summary_delta:
                reasoning_stages_emitted.add("answer_synthesis")
                yield GraphStreamItem(
                    kind="reasoning_delta",
                    stage="answer_synthesis",
                    delta=summary_delta,
                )
            text = message_chunk_text(message_chunk)
            if text:
                streamed_parts.append(text)
                yield GraphStreamItem(kind="delta", delta=text)
            continue
        if event_type == "updates" and isinstance(event_data, dict):
            fields = result_fields_from_update(event_data)
            if fields:
                final_result.update(fields)
                for stage, field_name in (
                    ("retrieval_planning", "retrieval_planning_summary"),
                    ("answer_synthesis", "answer_synthesis_summary"),
                ):
                    summary = fields.get(field_name)
                    if isinstance(summary, str) and stage not in reasoning_stages_emitted:
                        reasoning_stages_emitted.add(stage)
                        yield GraphStreamItem(
                            kind="reasoning_delta",
                            stage=stage,  # type: ignore[arg-type]
                            delta=summary,
                        )
                yield GraphStreamItem(kind="update", result=fields)

    if "reply" not in final_result and streamed_parts:
        final_result["reply"] = "".join(streamed_parts).strip()
    if "route" not in final_result:
        final_result["route"] = classify_messages(graph_input.get("messages", []))
    if (
        "reply" in final_result
        or "rag_retrieval_result" in final_result
        or "rag_retrieval_snapshot" in final_result
    ):
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


def stream_resumed_graph_items(
    *,
    graph_runner: GraphRunner,
    run_id: str,
    resume_value: dict[str, object],
    graph_context: dict[str, object],
):
    """Yield real message/update events while resuming one checkpoint thread."""
    stream = getattr(graph_runner, "stream", None)
    if not callable(stream):
        yield GraphStreamItem(
            kind="result",
            result=invoke_graph_runner_resume_collecting_updates(
                graph_runner=graph_runner,
                run_id=run_id,
                resume_value=resume_value,
                graph_context=graph_context,
            ),
        )
        return

    command = Command(resume=resume_value)
    config = {"configurable": {"thread_id": run_id}}
    streamed_parts: list[str] = []
    final_result: dict[str, Any] = {}
    reasoning_stages_emitted: set[str] = set()
    for event in stream(
        command,
        config=config,
        context=graph_context,
        stream_mode=["messages", "updates"],
        version="v2",
    ):
        event_type, event_data = stream_event_parts(event)
        if event_type == "messages":
            message_chunk, metadata = event_data
            if not should_emit_message_chunk(metadata):
                continue
            summary_delta = message_chunk_reasoning_summary_delta(message_chunk)
            if summary_delta:
                reasoning_stages_emitted.add("answer_synthesis")
                yield GraphStreamItem(
                    kind="reasoning_delta",
                    stage="answer_synthesis",
                    delta=summary_delta,
                )
            text = message_chunk_text(message_chunk)
            if text:
                streamed_parts.append(text)
                yield GraphStreamItem(kind="delta", delta=text)
            continue
        if event_type == "updates" and isinstance(event_data, dict):
            fields = result_fields_from_update(event_data)
            if fields:
                final_result.update(fields)
                for stage, field_name in (
                    ("retrieval_planning", "retrieval_planning_summary"),
                    ("answer_synthesis", "answer_synthesis_summary"),
                ):
                    summary = fields.get(field_name)
                    if isinstance(summary, str) and stage not in reasoning_stages_emitted:
                        reasoning_stages_emitted.add(stage)
                        yield GraphStreamItem(
                            kind="reasoning_delta",
                            stage=stage,  # type: ignore[arg-type]
                            delta=summary,
                        )
                yield GraphStreamItem(kind="update", result=fields)

    get_state = getattr(graph_runner, "get_state", None)
    if callable(get_state):
        snapshot = get_state(config)
        values = getattr(snapshot, "values", None)
        if isinstance(values, dict):
            final_result.update(values)
    if "reply" not in final_result and streamed_parts:
        final_result["reply"] = "".join(streamed_parts).strip()
    if not final_result:
        raise RuntimeError("resumed graph stream ended without state")
    yield GraphStreamItem(kind="result", result=final_result)


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
    if "__interrupt__" in update:
        fields["__interrupt__"] = update["__interrupt__"]
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
            "retrieval_records",
            "retrieved_context",
            "document_artifacts",
            "document_selection_options",
        ):
            value = node_update.get(field_name)
            if isinstance(value, list):
                fields[field_name] = value
        for field_name in (
            "rag_retrieval_result",
            "rag_retrieval_snapshot",
            "rag_halt_before_response",
            "retrieval_route",
            "answer_mode",
            "document_scope",
            "document_workspace_expires_at",
            "document_selection_option_count",
            "document_selection_library_count",
            "document_selection_schema_version",
            "document_selection_interaction_id",
            "document_selection_reason_code",
            "document_selection_refinement_attempts",
            "document_selection_refinement_allowed",
            "document_selection_browse_allowed",
            "document_selection_needs_resolution",
            "document_selection_answer_kind",
            "document_selection_preparation_status",
            "document_reference_query",
            "selected_document_id",
            "full_document_requested",
            "rag_retrieval_tool",
            "rag_retrieval_tool_reason",
            "retrieval_planning_summary",
            "answer_synthesis_summary",
            "full_document_target_status",
            "full_document_next_cursor",
        ):
            if field_name in node_update:
                fields[field_name] = node_update[field_name]
        document_coverage = node_update.get("document_coverage")
        if isinstance(document_coverage, dict):
            fields["document_coverage"] = document_coverage
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


def message_chunk_reasoning_summary_delta(message_chunk: Any) -> str:
    """Extract only Responses API summary deltas from an answer-model chunk."""
    content = getattr(message_chunk, "content", None)
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "reasoning":
            continue
        summary = block.get("summary")
        if not isinstance(summary, list):
            continue
        parts.extend(
            item["text"]
            for item in summary
            if isinstance(item, dict)
            and item.get("type") == "summary_text"
            and isinstance(item.get("text"), str)
        )
    return "".join(parts)


def fallback_answer_deltas(reply: str) -> list[str]:
    words = reply.split(" ")
    if len(words) <= 1:
        return [reply] if reply else []
    return [f"{word} " for word in words[:-1]] + [words[-1]]
