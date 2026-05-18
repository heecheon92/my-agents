"""Reference implementation for the Support Ticket Router simulated agent.

This version keeps the same learning pattern as ``graph.py``:

1. classify a support ticket;
2. route by a conditional edge;
3. stream the chosen response node's LLM output in the terminal.

The main differences are simpler state handling, explicit public/internal/output
schemas, an explicit conditional route map, and a thin streaming CLI adapter.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from my_agents.settings import get_settings

RouteName = Literal["billing", "technical", "account", "general"]
RESPONSE_NODES = {"billing", "technical", "account", "general"}

settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model)


class RouteDecision(BaseModel):
    """Structured classifier output used by the conditional edge."""

    route: RouteName = Field(description="Support route that should handle the ticket.")
    reason: str = Field(description="Brief reason for the selected route.")


class SupportTicketRouterInput(TypedDict):
    """Public graph input: callers only provide the raw support ticket."""

    ticket: str


class SupportTicketRouterState(TypedDict):
    """Internal graph state shared by nodes.

    Node functions receive this full state, even when they only read one field.
    The graph structure guarantees that ``route_decision`` exists before the
    routing function and response nodes run.
    """

    ticket: str
    route_decision: NotRequired[RouteDecision]
    final_response: NotRequired[str]


class SupportTicketRouterOutput(TypedDict):
    """Public graph output: callers only need the final response."""

    final_response: str


def classify_ticket(state: SupportTicketRouterState) -> dict[str, RouteDecision]:
    """Classify the ticket and store a structured route decision."""
    ticket = state["ticket"]
    raw_decision = llm.with_structured_output(RouteDecision).invoke(
        [
            SystemMessage(
                content=(
                    "You classify support tickets. Choose exactly one route: "
                    "billing, technical, account, or general. Keep the reason brief."
                )
            ),
            HumanMessage(content=f"Support ticket:\n{ticket}"),
        ]
    )
    decision = RouteDecision.model_validate(raw_decision)
    return {"route_decision": decision}


def choose_route(state: SupportTicketRouterState) -> RouteName:
    """Return the route label consumed by ``add_conditional_edges``."""
    return state["route_decision"].route


def billing(state: SupportTicketRouterState) -> dict[str, str]:
    """Draft a billing-focused support response."""
    return {"final_response": _invoke_support_role(state, role="billing specialist")}


def technical(state: SupportTicketRouterState) -> dict[str, str]:
    """Draft a technical-support response."""
    return {"final_response": _invoke_support_role(state, role="technical support engineer")}


def account(state: SupportTicketRouterState) -> dict[str, str]:
    """Draft an account-focused support response."""
    return {"final_response": _invoke_support_role(state, role="account support specialist")}


def general(state: SupportTicketRouterState) -> dict[str, str]:
    """Draft a general support response."""
    return {"final_response": _invoke_support_role(state, role="general support assistant")}


def _invoke_support_role(state: SupportTicketRouterState, *, role: str) -> str:
    """Call the model for the selected simulated support role.

    This helper is intentionally small: it removes repeated prompt plumbing while
    keeping the graph nodes explicit. It does not hide graph state transitions.
    """
    ticket = state["ticket"]
    decision = state["route_decision"]
    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    f"You are a simulated {role}. Respond to the support ticket. "
                    "Be concise, practical, and honest that this is a simulated reply."
                )
            ),
            HumanMessage(
                content=(
                    f"Ticket:\n{ticket}\n\n"
                    f"Classifier route: {decision.route}\n"
                    f"Classifier reason: {decision.reason}"
                )
            ),
        ]
    )
    return response.content if isinstance(response.content, str) else str(response.content)


builder = StateGraph(
    SupportTicketRouterState,
    input_schema=SupportTicketRouterInput,
    output_schema=SupportTicketRouterOutput,
)
builder.add_node("classifier", classify_ticket)
builder.add_node("billing", billing)
builder.add_node("technical", technical)
builder.add_node("account", account)
builder.add_node("general", general)

builder.add_edge(START, "classifier")
builder.add_conditional_edges(
    "classifier",
    choose_route,
    {
        "billing": "billing",
        "technical": "technical",
        "account": "account",
        "general": "general",
    },
)
builder.add_edge("billing", END)
builder.add_edge("technical", END)
builder.add_edge("account", END)
builder.add_edge("general", END)

graph = builder.compile()


def stream_response(user_input: str) -> str:
    """Print final response chunks as they arrive and return the full text.

    ``stream_mode="messages"`` emits LLM message chunks from model calls inside
    graph nodes. The classifier may also call an LLM, so this adapter filters for
    the final response nodes only.
    """
    chunks: list[str] = []
    initial_state: SupportTicketRouterInput = {"ticket": user_input}

    for message_chunk, metadata in graph.stream(initial_state, stream_mode="messages"):
        node_name = metadata.get("langgraph_node")
        if node_name not in RESPONSE_NODES:
            continue

        content = message_chunk.content
        if isinstance(content, str) and content:
            print(content, end="", flush=True)
            chunks.append(content)

    print()
    return "".join(chunks)


if __name__ == "__main__":
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            stream_response(user_input)
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
