"""LangGraph implementation for the personal assistant backend."""

from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from my_agents.agents.capabilities import AgentCapability, get_capability_for_route
from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.memory_recall import (
    AssistantRuntimeContext,
    retrieve_memory_context,
)
from my_agents.agents.general_assistant.rag_retrieval import (
    retrieve_rag_context,
    select_after_rag_context,
    skip_rag_context,
)
from my_agents.agents.general_assistant.responders import get_response_provider
from my_agents.agents.general_assistant.retrieval_gate import (
    RetrievalSourceDecider,
    RetrievalSourceDecision,
    get_retrieval_source_decider,
)
from my_agents.agents.rag_agent import RagAgentRetrievalResult
from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute
from my_agents.schemas import RouteDecision

HANDLED_BY = "personal_assistant_graph"


class AssistantState(TypedDict, total=False):
    """State passed through the personal assistant graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    route: RouteDecision
    capability: AgentCapability
    reply: str
    handled_by: str
    principal_id: str
    conversation_id: str
    retrieved_chunk_ids: list[str]
    retrieved_context: list[dict[str, object]]
    memory_context: list[dict[str, object]]
    source_conflicts: list[dict[str, object]]
    rag_retrieval_result: RagAgentRetrievalResult
    retrieval_source_decision: RetrievalSourceDecision
    rag_halt_before_response: bool
    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    debug_empty_openai_response: bool
    document_artifacts: list[dict[str, object]]
    document_workspace_expires_at: str


def classify_request(state: AssistantState) -> AssistantState:
    """Classify the message into a route label using deterministic local rules."""
    route = classify_messages(state.get("messages", []))
    capability = get_capability_for_route(route.label)
    return {"route": route, "capability": capability, "handled_by": HANDLED_BY}


def select_response_node(state: AssistantState) -> str:
    """Map the route label to a graph response node name."""
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


def _compose_reply(
    state: AssistantState,
    runtime: Runtime[AssistantRuntimeContext],
    guidance: str,
) -> AssistantState:
    route = state["route"]
    runtime_context = runtime.context or {}
    reasoning_mode = runtime_context.get("reasoning_mode", "standard")
    reasoning_effort = runtime_context.get("reasoning_effort", "medium")
    workspace_runtime = runtime_context.get("document_workspace_runtime")
    workspace_compose_reply = getattr(workspace_runtime, "compose_reply", None)
    if callable(workspace_compose_reply):
        result = workspace_compose_reply(
            messages=state.get("messages", []),
            route=route,
            capability=state["capability"],
            guidance=guidance,
            retrieved_context=state.get("retrieved_context", []),
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
        retrieved_context=state.get("retrieved_context", []),
        memory_context=state.get("memory_context", []),
        source_conflicts=state.get("source_conflicts", []),
        answer_mode=state.get("answer_mode", "general_knowledge"),
        debug_empty_response=state.get("debug_empty_openai_response", False),
        reasoning_mode=reasoning_mode,
        reasoning_effort=reasoning_effort,
    )
    return {"reply": reply}


def build_graph():
    """Build and compile the retrieval-enabled product assistant graph.

    Conversation-run services must invoke this graph with `graph_context_for_run(...)`
    so the RAG Agent runtime and authorized knowledge-base selection are present.
    """
    return _build_graph(include_rag=True)


def build_legacy_chat_graph():
    """Build the unauthenticated legacy/dev chat graph without document retrieval."""
    return _build_graph(include_rag=False)


def _build_graph(*, include_rag: bool):
    graph = StateGraph(AssistantState, context_schema=AssistantRuntimeContext)
    graph.add_node("classify_request", classify_request)
    if include_rag:
        graph.add_node("decide_retrieval_source", decide_retrieval_source)
        graph.add_node("retrieve_rag_context", retrieve_rag_context)
        graph.add_node("skip_rag_context", skip_rag_context)
    graph.add_node("retrieve_memory", retrieve_memory_context)
    graph.add_node("respond_general", respond_general)
    graph.add_node("respond_research", respond_research)

    graph.add_edge(START, "classify_request")
    if include_rag:
        graph.add_edge("classify_request", "decide_retrieval_source")
        graph.add_conditional_edges(
            "decide_retrieval_source",
            select_retrieval_source,
            {
                "knowledge_base": "retrieve_rag_context",
                "bypass": "skip_rag_context",
            },
        )
        for retrieval_node in ("retrieve_rag_context", "skip_rag_context"):
            graph.add_conditional_edges(
                retrieval_node,
                select_after_rag_context,
                {
                    "retrieve_memory": "retrieve_memory",
                    "end": END,
                },
            )
    else:
        graph.add_edge("classify_request", "retrieve_memory")
    graph.add_conditional_edges(
        "retrieve_memory",
        select_response_node,
        {
            "respond_general": "respond_general",
            "respond_research": "respond_research",
        },
    )
    for node_name in ("respond_general", "respond_research"):
        graph.add_edge(node_name, END)

    return graph.compile()
