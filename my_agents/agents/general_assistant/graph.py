"""LangGraph implementation for the personal assistant backend."""

import re
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage, BaseMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langsmith import tracing_context

from my_agents.agents.capabilities import AgentCapability, get_capability_for_route
from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.memory_recall import (
    AssistantRuntimeContext,
    latest_human_text,
    retrieve_memory_context,
)
from my_agents.agents.general_assistant.rag_retrieval import (
    full_document_unavailable_state,
    prepare_document_selection,
    prepare_full_document_read,
    request_document_selection,
    resolve_full_document_target,
    retrieve_rag_context,
    retrieve_selected_rag_context,
    select_after_document_selection,
    select_after_full_document_read,
    select_after_full_document_target,
    select_after_rag_context,
    skip_rag_context,
)
from my_agents.agents.general_assistant.responders import get_response_provider
from my_agents.agents.general_assistant.retrieval_gate import (
    RetrievalSourceDecider,
    RetrievalSourceDecision,
    get_retrieval_source_decider,
)
from my_agents.knowledge.routing import (
    AnswerMode,
    DocumentScope,
    RetrievalRoute,
    is_comprehensive_document_request,
)
from my_agents.schemas import RouteDecision

HANDLED_BY = "personal_assistant_graph"
GRAPH_VERSION = "general-assistant-checkpoint-v2"


class AssistantState(TypedDict, total=False):
    """State passed through the personal assistant graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    route: RouteDecision
    capability: AgentCapability
    reply: str
    handled_by: str
    principal_id: str
    conversation_id: str
    run_id: str
    retrieved_chunk_ids: list[str]
    retrieval_records: list[dict[str, object]]
    retrieved_context: list[dict[str, object]]
    memory_context: list[dict[str, object]]
    source_conflicts: list[dict[str, object]]
    rag_retrieval_snapshot: dict[str, object]
    retrieval_source_decision: RetrievalSourceDecision
    rag_halt_before_response: bool
    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    debug_empty_openai_response: bool
    document_artifacts: list[dict[str, object]]
    document_workspace_expires_at: str
    document_selection_options: list[dict[str, object]]
    document_selection_option_count: int
    selected_document_id: str
    document_selection_hitl_allowed: bool
    full_document_retrieval_enabled: bool
    full_document_requested: bool
    full_document_target_status: str
    full_document_next_cursor: str | None
    document_coverage: dict[str, object]


def classify_request(state: AssistantState) -> AssistantState:
    """Classify the message into a route label using deterministic local rules."""
    route = classify_messages(state.get("messages", []))
    capability = get_capability_for_route(route.label)
    return {
        "route": route,
        "capability": capability,
        "handled_by": HANDLED_BY,
        "full_document_requested": is_comprehensive_document_request(
            latest_human_text(_state_messages(state.get("messages", [])))
        ),
    }


def select_response_node(state: AssistantState) -> str:
    """Map the route label to a graph response node name."""
    if (
        state.get("full_document_requested") is True
        and state.get("full_document_target_status") == "resolved"
        and isinstance(state.get("document_coverage"), dict)
    ):
        return "respond_full_document"
    route = state["route"].label
    return {
        "general_assistant": "respond_general",
        "research_helper": "respond_research",
    }[route]


def decide_retrieval_source(
    state: AssistantState,
    runtime: Runtime[AssistantRuntimeContext],
) -> AssistantState:
    """Decide whether this turn should enter private knowledge-base retrieval."""
    context = runtime.context or {}
    selection_context = context.get("knowledge_base_selection")
    if selection_context is None:
        raise RuntimeError(
            "general_assistant graph requires RAG Agent runtime context; "
            "use graph_context_for_run for conversation runs or build_legacy_chat_graph "
            "for unauthenticated no-KB chat."
        )
    if (
        context.get("document_workspace_runtime") is not None
        and selection_context.mode != "selected"
    ):
        return {
            "retrieval_source_decision": RetrievalSourceDecision(
                source="bypass",
                reason=("temporary conversation attachments are the explicit source for this turn"),
            )
        }
    decider = _retrieval_source_decider(context.get("retrieval_source_decider"))
    decision = decider.decide(
        messages=_state_messages(state.get("messages", [])),
        selection_context=selection_context,
    )
    return {"retrieval_source_decision": decision}


def select_retrieval_source(state: AssistantState) -> str:
    """Route to RAG retrieval only when the source-selection gate requests it."""
    decision = state["retrieval_source_decision"]
    if (
        decision.source == "knowledge_base"
        and state.get("full_document_retrieval_enabled") is True
        and state.get("full_document_requested") is True
    ):
        return "full_document"
    return decision.source


def _retrieval_source_decider(candidate: object) -> RetrievalSourceDecider:
    decide = getattr(candidate, "decide", None)
    if callable(decide):
        return candidate  # type: ignore[return-value]
    return get_retrieval_source_decider()


def _state_messages(messages: Sequence[Any]) -> list[BaseMessage]:
    return [message for message in messages if isinstance(message, BaseMessage)]


def respond_general(
    state: AssistantState,
    runtime: Runtime[AssistantRuntimeContext],
) -> AssistantState:
    return _compose_reply(state, runtime, _general_response_guidance(state))


def _general_response_guidance(state: AssistantState) -> str:
    if state.get("retrieval_route") == "clarification_required":
        return (
            "The retrieval tool could not determine which authorized document the "
            "user means. Ask one concise clarification question that prompts the "
            "user to identify the intended document or source. Do not answer the "
            "document-specific question yet. Match the user's language."
        )
    return "I can help organize the request and suggest a practical next step."


def respond_research(
    state: AssistantState,
    runtime: Runtime[AssistantRuntimeContext],
) -> AssistantState:
    return _compose_reply(
        state,
        runtime,
        "A useful research pass is to list source questions, prefer primary docs, "
        "and capture findings with links.",
    )


def respond_full_document(
    state: AssistantState,
    runtime: Runtime[AssistantRuntimeContext],
) -> AssistantState:
    """Compose from one authorized range without checkpointing its raw text."""
    runtime_context = runtime.context or {}
    rag_runtime = runtime_context.get("rag_runtime")
    selection_context = runtime_context.get("knowledge_base_selection")
    user_id = runtime_context.get("user_id") or state.get("principal_id")
    document_id = state.get("selected_document_id")
    coverage = state.get("document_coverage")
    if (
        rag_runtime is None
        or selection_context is None
        or not isinstance(user_id, str)
        or not isinstance(document_id, str)
        or not isinstance(coverage, dict)
    ):
        raise RuntimeError("full-document response requires prepared authorized coverage")
    read_result = rag_runtime.read_full_document_range(
        user_id=user_id,
        document_id=document_id,
        selection_context=selection_context,
        full_document_max_chars=int(runtime_context.get("full_document_max_chars") or 24_000),
        range_chars=int(runtime_context.get("full_document_range_chars") or 12_000),
    )
    if read_result is None or not read_result.content or not read_result.retrieved_chunks:
        return full_document_unavailable_state(
            state,
            runtime,
            reason="prepared full-document evidence is no longer available",
        )
    if (
        read_result.start_offset != coverage.get("start_offset")
        or read_result.end_offset != coverage.get("end_offset")
        or read_result.total_chars != coverage.get("total_chars")
    ):
        return full_document_unavailable_state(
            state,
            runtime,
            reason="document changed after full-document coverage was prepared",
        )
    mode = str(coverage.get("mode") or "partial")
    guidance = (
        "The application supplied the complete normalized extracted text for the resolved "
        "authorized document. Cover the user's comprehensive request across the whole text."
        if mode == "complete"
        else (
            "The application supplied only the first bounded range of a larger authorized "
            "document. Answer only from that range and do not claim complete-document coverage."
        )
    )
    with tracing_context(enabled=False):
        response = _compose_reply(
            state,
            runtime,
            guidance,
            retrieved_context_override=[
                {
                    "document_id": read_result.document.id,
                    "knowledge_base_id": read_result.document.knowledge_base_id,
                    "title": read_result.document.title,
                    "snippet": read_result.content,
                    "source_filename": read_result.document.source_filename,
                    "source": "full_document",
                }
            ],
        )
    if mode == "partial":
        response["reply"] = _partial_coverage_disclosure(
            str(response["reply"]),
            latest_human_text(_state_messages(state.get("messages", []))),
            end_offset=read_result.end_offset,
            total_chars=read_result.total_chars,
        )
    return response


def _compose_reply(
    state: AssistantState,
    runtime: Runtime[AssistantRuntimeContext],
    guidance: str,
    retrieved_context_override: Sequence[dict[str, Any]] | None = None,
) -> AssistantState:
    route = state["route"]
    runtime_context = runtime.context or {}
    reasoning_mode = runtime_context.get("reasoning_mode", "standard")
    reasoning_effort = runtime_context.get("reasoning_effort", "medium")
    workspace_runtime = runtime_context.get("document_workspace_runtime")
    retrieved_context = (
        list(retrieved_context_override)
        if retrieved_context_override is not None
        else state.get("retrieved_context", [])
    )
    workspace_compose_reply = getattr(workspace_runtime, "compose_reply", None)
    if callable(workspace_compose_reply):
        result = workspace_compose_reply(
            messages=state.get("messages", []),
            route=route,
            capability=state["capability"],
            guidance=guidance,
            retrieved_context=retrieved_context,
            memory_context=state.get("memory_context", []),
            source_conflicts=state.get("source_conflicts", []),
            answer_mode=state.get("answer_mode", "general_knowledge"),
            reasoning_mode=reasoning_mode,
            reasoning_effort=reasoning_effort,
        )
        return {
            "reply": result.reply,
            "document_artifacts": [
                artifact.model_dump(mode="json") for artifact in result.artifacts
            ],
            "document_workspace_expires_at": result.workspace_expires_at.isoformat(),
        }
    reply = get_response_provider().compose_reply(
        messages=state.get("messages", []),
        route=route,
        capability=state["capability"],
        guidance=guidance,
        retrieved_context=retrieved_context,
        memory_context=state.get("memory_context", []),
        source_conflicts=state.get("source_conflicts", []),
        answer_mode=state.get("answer_mode", "general_knowledge"),
        debug_empty_response=state.get("debug_empty_openai_response", False),
        reasoning_mode=reasoning_mode,
        reasoning_effort=reasoning_effort,
    )
    return {"reply": reply}


def _partial_coverage_disclosure(
    reply: str,
    user_message: str,
    *,
    end_offset: int,
    total_chars: int,
) -> str:
    if re.search(r"[\uac00-\ud7a3]", user_message):
        notice = (
            f"부분 검토 안내: 큰 문서의 0-{end_offset}자만 검토했습니다"
            f"(전체 {total_chars}자). 아직 문서 전체 검토 결과는 아닙니다."
        )
    else:
        notice = (
            f"Partial-review notice: I reviewed characters 0-{end_offset} of {total_chars}. "
            "This is not yet a complete-document review."
        )
    return f"{notice}\n\n{reply.strip()}"


def build_graph(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    document_selection_hitl_enabled: bool = False,
):
    """Build and compile the retrieval-enabled product assistant graph.

    Conversation-run services must invoke this graph with `graph_context_for_run(...)`
    so the RAG Agent runtime and authorized knowledge-base selection are present.
    """
    return _build_graph(
        include_rag=True,
        checkpointer=checkpointer,
        store=store,
        document_selection_hitl_enabled=document_selection_hitl_enabled,
    )


def build_legacy_chat_graph():
    """Build the unauthenticated legacy/dev chat graph without document retrieval."""
    return _build_graph(include_rag=False)


def _build_graph(
    *,
    include_rag: bool,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    document_selection_hitl_enabled: bool = False,
):
    graph = StateGraph(AssistantState, context_schema=AssistantRuntimeContext)
    graph.add_node("classify_request", classify_request)
    if include_rag:
        graph.add_node("decide_retrieval_source", decide_retrieval_source)
        graph.add_node("retrieve_rag_context", retrieve_rag_context)
        graph.add_node("skip_rag_context", skip_rag_context)
        graph.add_node("resolve_full_document_target", resolve_full_document_target)
        graph.add_node("prepare_full_document_read", prepare_full_document_read)
        if document_selection_hitl_enabled:
            graph.add_node("prepare_document_selection", prepare_document_selection)
            graph.add_node("request_document_selection", request_document_selection)
            graph.add_node("retrieve_selected_rag_context", retrieve_selected_rag_context)
    graph.add_node("retrieve_memory", retrieve_memory_context)
    graph.add_node("respond_general", respond_general)
    graph.add_node("respond_research", respond_research)
    if include_rag:
        graph.add_node("respond_full_document", respond_full_document)

    graph.add_edge(START, "classify_request")
    if include_rag:
        graph.add_edge("classify_request", "decide_retrieval_source")
        graph.add_conditional_edges(
            "decide_retrieval_source",
            select_retrieval_source,
            {
                "knowledge_base": "retrieve_rag_context",
                "bypass": "skip_rag_context",
                "full_document": "resolve_full_document_target",
            },
        )
        for retrieval_node in ("retrieve_rag_context", "skip_rag_context"):
            graph.add_conditional_edges(
                retrieval_node,
                lambda state: select_after_rag_context(
                    state,
                    document_selection_hitl_enabled=document_selection_hitl_enabled,
                ),
                (
                    {
                        "retrieve_memory": "retrieve_memory",
                        "end": END,
                        **(
                            {"prepare_document_selection": "prepare_document_selection"}
                            if document_selection_hitl_enabled
                            else {}
                        ),
                    }
                ),
            )
        if document_selection_hitl_enabled:
            graph.add_edge("prepare_document_selection", "request_document_selection")
            graph.add_conditional_edges(
                "request_document_selection",
                select_after_document_selection,
                {
                    "resolve_full_document_target": "resolve_full_document_target",
                    "retrieve_selected_rag_context": "retrieve_selected_rag_context",
                },
            )
            graph.add_conditional_edges(
                "retrieve_selected_rag_context",
                lambda state: select_after_rag_context(
                    state,
                    document_selection_hitl_enabled=False,
                ),
                {"retrieve_memory": "retrieve_memory", "end": END},
            )
        graph.add_conditional_edges(
            "resolve_full_document_target",
            lambda state: select_after_full_document_target(
                state,
                document_selection_hitl_enabled=document_selection_hitl_enabled,
            ),
            {
                "prepare_full_document_read": "prepare_full_document_read",
                "retrieve_memory": "retrieve_memory",
                "end": END,
                **(
                    {"prepare_document_selection": "prepare_document_selection"}
                    if document_selection_hitl_enabled
                    else {}
                ),
            },
        )
        graph.add_conditional_edges(
            "prepare_full_document_read",
            select_after_full_document_read,
            {"retrieve_memory": "retrieve_memory", "end": END},
        )
    else:
        graph.add_edge("classify_request", "retrieve_memory")
    graph.add_conditional_edges(
        "retrieve_memory",
        select_response_node,
        {
            "respond_general": "respond_general",
            "respond_research": "respond_research",
            **({"respond_full_document": "respond_full_document"} if include_rag else {}),
        },
    )
    response_nodes = ["respond_general", "respond_research"]
    if include_rag:
        response_nodes.append("respond_full_document")
    for node_name in response_nodes:
        graph.add_edge(node_name, END)

    return graph.compile(checkpointer=checkpointer, store=store)
