"""LangGraph implementation for the personal assistant backend."""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from my_agents.agents.capabilities import AgentCapability, get_capability_for_route
from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.memory_recall import (
    AssistantRuntimeContext,
    retrieve_memory_context,
)
from my_agents.agents.general_assistant.responders import get_response_provider
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
    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    debug_empty_openai_response: bool


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


def respond_general(state: AssistantState) -> AssistantState:
    return {
        "reply": _compose_reply(
            state,
            "I can help organize the request and suggest a practical next step.",
        )
    }


def respond_research(state: AssistantState) -> AssistantState:
    return {
        "reply": _compose_reply(
            state,
            "A useful research pass is to list source questions, prefer primary docs, "
            "and capture findings with links.",
        )
    }


def _compose_reply(state: AssistantState, guidance: str) -> str:
    route = state["route"]
    return get_response_provider().compose_reply(
        messages=state.get("messages", []),
        route=route,
        capability=state["capability"],
        guidance=guidance,
        retrieved_context=state.get("retrieved_context", []),
        memory_context=state.get("memory_context", []),
        source_conflicts=state.get("source_conflicts", []),
        answer_mode=state.get("answer_mode", "general_knowledge"),
        debug_empty_response=state.get("debug_empty_openai_response", False),
    )


def build_graph():
    """Build and compile the real LangGraph StateGraph."""
    graph = StateGraph(AssistantState, context_schema=AssistantRuntimeContext)
    graph.add_node("classify_request", classify_request)
    graph.add_node("retrieve_memory", retrieve_memory_context)
    graph.add_node("respond_general", respond_general)
    graph.add_node("respond_research", respond_research)

    graph.add_edge(START, "classify_request")
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
