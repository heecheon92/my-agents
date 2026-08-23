"""Helpers for invoking LangGraph runners with runtime-only dependencies."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from typing import Any

from langgraph.types import Command
from sqlalchemy.orm import Session

from my_agents.agents.rag_agent import SqlAlchemyRagAgentRuntime
from my_agents.api.assistant import GraphRunner
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.memory.runtime import SqlAlchemyMemoryRuntime
from my_agents.observability.metrics import track_graph_invocation
from my_agents.settings import ReasoningEffort, ReasoningMode, get_settings


class GraphRunnerExecutionError(RuntimeError):
    """Graph execution failed after emitting partial state updates."""

    def __init__(
        self,
        original_exception: Exception,
        *,
        partial_state: dict[str, object] | None = None,
    ) -> None:
        super().__init__(str(original_exception))
        self.original_exception = original_exception
        self.partial_state = partial_state or {}


def graph_context_for_run(
    *,
    db: Session,
    user_id: str,
    selection_context: KnowledgeBaseSelectionContext,
    document_workspace_runtime: object | None = None,
    reasoning_mode: ReasoningMode = "standard",
    reasoning_effort: ReasoningEffort = "medium",
) -> dict[str, object]:
    """Build LangGraph runtime context for one conversation run.

    The context carries non-checkpointed runtime dependencies. This keeps memory recall
    graph-owned without storing DB sessions or adapter objects in graph state.
    """
    settings = get_settings()
    context: dict[str, object] = {
        "user_id": user_id,
        "memory_runtime": SqlAlchemyMemoryRuntime(db),
        "rag_runtime": SqlAlchemyRagAgentRuntime(db),
        "knowledge_base_selection": selection_context,
        "reasoning_mode": reasoning_mode,
        "reasoning_effort": reasoning_effort,
        "full_document_max_chars": settings.full_document_max_chars,
        "full_document_range_chars": settings.full_document_range_chars,
    }
    if document_workspace_runtime is not None:
        context["document_workspace_runtime"] = document_workspace_runtime
    return context


def invoke_graph_runner(
    *,
    graph_runner: GraphRunner,
    graph_input: dict[str, object],
    graph_context: dict[str, object] | None = None,
) -> dict:
    """Invoke a graph runner, passing LangGraph `context` only when supported."""
    invoke = graph_runner.invoke
    kwargs = _thread_config_kwargs(graph_input)
    with track_graph_invocation("invoke"):
        if graph_context is not None and _supports_keyword(invoke, "context"):
            return invoke(graph_input, context=graph_context, **kwargs)
        return invoke(graph_input, **kwargs)


def invoke_graph_runner_collecting_updates(
    *,
    graph_runner: GraphRunner,
    graph_input: dict[str, object],
    graph_context: dict[str, object] | None = None,
) -> dict:
    """Invoke a graph while preserving node updates if execution later fails.

    LangGraph `.invoke(...)` returns only the final state. For conversation runs we
    need graph-owned memory provenance even when a later provider node fails, so
    compiled graphs are driven through update streaming and merged into a compact
    final state. Simple test doubles that do not expose `.stream(...)` still use
    the normal invoke path.
    """
    stream = getattr(graph_runner, "stream", None)
    if not callable(stream):
        return invoke_graph_runner(
            graph_runner=graph_runner,
            graph_input=graph_input,
            graph_context=graph_context,
        )

    partial_state: dict[str, object] = {}
    emitted_stream_event = False
    try:
        with track_graph_invocation("stream_updates"):
            for event in stream_graph_runner(
                stream=stream,
                graph_input=graph_input,
                graph_context=graph_context,
                stream_mode="updates",
                version="v2",
            ):
                emitted_stream_event = True
                _merge_update_event(partial_state, event)
    except Exception as exc:
        raise GraphRunnerExecutionError(exc, partial_state=partial_state) from exc

    if emitted_stream_event:
        return partial_state
    return invoke_graph_runner(
        graph_runner=graph_runner,
        graph_input=graph_input,
        graph_context=graph_context,
    )


def invoke_graph_runner_resume_collecting_updates(
    *,
    graph_runner: GraphRunner,
    run_id: str,
    resume_value: dict[str, object],
    graph_context: dict[str, object],
) -> dict:
    """Resume one checkpoint thread and preserve all node updates/interrupts."""
    stream = getattr(graph_runner, "stream", None)
    command = Command(resume=resume_value)
    config = {"configurable": {"thread_id": run_id}}
    if not callable(stream):
        return graph_runner.invoke(command, config=config, context=graph_context)
    partial_state: dict[str, object] = {}
    try:
        for event in stream(
            command,
            config=config,
            context=graph_context,
            stream_mode="updates",
            version="v2",
        ):
            _merge_update_event(partial_state, event)
    except Exception as exc:
        raise GraphRunnerExecutionError(exc, partial_state=partial_state) from exc
    get_state = getattr(graph_runner, "get_state", None)
    if callable(get_state):
        snapshot = get_state(config)
        values = getattr(snapshot, "values", None)
        if isinstance(values, dict):
            return values
    return partial_state


def stream_graph_runner(
    *,
    stream: Callable[..., Iterator[Any]],
    graph_input: dict[str, object],
    graph_context: dict[str, object] | None = None,
    **kwargs: Any,
) -> Iterator[Any]:
    """Stream from a graph runner, passing LangGraph `context` only when supported."""
    if graph_context is not None and _supports_keyword(stream, "context"):
        return stream(
            graph_input,
            context=graph_context,
            **_thread_config_kwargs(graph_input),
            **kwargs,
        )
    return stream(graph_input, **_thread_config_kwargs(graph_input), **kwargs)


def _thread_config_kwargs(graph_input: object) -> dict[str, object]:
    if not isinstance(graph_input, dict):
        return {}
    run_id = graph_input.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return {}
    return {"config": {"configurable": {"thread_id": run_id}}}


def _supports_keyword(callable_obj: Callable[..., Any], keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters
    except TypeError, ValueError:
        return True
    if keyword in parameters:
        return True
    return any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def _merge_update_event(partial_state: dict[str, object], event: Any) -> None:
    event_type, event_data = _stream_event_parts(event)
    if event_type != "updates" or not isinstance(event_data, dict):
        return
    if "__interrupt__" in event_data:
        partial_state["__interrupt__"] = event_data["__interrupt__"]
    for node_update in event_data.values():
        if isinstance(node_update, dict):
            partial_state.update(node_update)


def _stream_event_parts(event: Any) -> tuple[str | None, Any]:
    if isinstance(event, dict):
        return event.get("type"), event.get("data")
    if isinstance(event, tuple) and len(event) == 2:
        return event
    return None, None
