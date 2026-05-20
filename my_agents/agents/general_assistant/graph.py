"""LangGraph implementation for the personal assistant backend."""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from my_agents.agents.capabilities import AgentCapability, get_capability_for_route
from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.responders import get_response_provider
from my_agents.schemas import RouteDecision

HANDLED_BY = "personal_assistant_graph"


class AssistantState(TypedDict, total=False):
    """State passed through the personal assistant graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    route: RouteDecision
    capability: AgentCapability
    reply: str
    handled_by: str
    retrieved_context: list[dict[str, object]]
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
        "learning_coach": "respond_learning",
        "research_helper": "respond_research",
        "project_planner": "respond_project",
        "career_helper": "respond_career",
    }[route]


def respond_general(state: AssistantState) -> AssistantState:
    return {
        "reply": _compose_reply(
            state,
            "I can help organize the request and suggest a practical next step.",
        )
    }


def respond_learning(state: AssistantState) -> AssistantState:
    return {
        "reply": _compose_reply(
            state,
            "A useful learning path is to define the concept, build a tiny example, then test it.",
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


def respond_project(state: AssistantState) -> AssistantState:
    return {
        "reply": _compose_reply(
            state,
            "A useful planning pass is to name the goal, split the next milestone, "
            "and define verification evidence.",
        )
    }


def respond_career(state: AssistantState) -> AssistantState:
    return {
        "reply": _compose_reply(
            state,
            "A useful career-material pass is to clarify the audience, the evidence, "
            "and the outcome you want the wording to prove.",
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
        debug_empty_response=state.get("debug_empty_openai_response", False),
    )


def build_graph():
    """Build and compile the real LangGraph StateGraph."""
    graph = StateGraph(AssistantState)
    graph.add_node("classify_request", classify_request)
    graph.add_node("respond_general", respond_general)
    graph.add_node("respond_learning", respond_learning)
    graph.add_node("respond_research", respond_research)
    graph.add_node("respond_project", respond_project)
    graph.add_node("respond_career", respond_career)

    graph.add_edge(START, "classify_request")
    graph.add_conditional_edges(
        "classify_request",
        select_response_node,
        {
            "respond_general": "respond_general",
            "respond_learning": "respond_learning",
            "respond_research": "respond_research",
            "respond_project": "respond_project",
            "respond_career": "respond_career",
        },
    )
    for node_name in (
        "respond_general",
        "respond_learning",
        "respond_research",
        "respond_project",
        "respond_career",
    ):
        graph.add_edge(node_name, END)

    return graph.compile()
