"""Bootstrap graph module for the Support Ticket Router simulated agent.

This file intentionally starts with only a terminal loop. Replace `respond()` with
LangGraph state, nodes, routing, and graph invocation after the learning pattern is
clear.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from langchain.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from my_agents.settings import get_settings

AGENT_NAME = "support_ticket_router"

settings = get_settings()

llm = ChatOpenAI(model=settings.openai_model)


class RouteDecision(BaseModel):
    route: Literal["billing", "technical", "account", "general"]
    reason: str


class SupportTicketRouterState(TypedDict):
    ticket: str
    route_decision: NotRequired[RouteDecision]
    final_response: NotRequired[str]


class RouteDecisionUpdate(TypedDict):
    route_decision: RouteDecision


def classifier(state: SupportTicketRouterState) -> RouteDecisionUpdate:
    ticket = state["ticket"]
    if not ticket:
        raise RuntimeError("Ticket should have been created")

    system_prompt = """
    Classify user's ticket where to redirect.
    """

    raw_decision = llm.with_structured_output(RouteDecision).invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"ticket: {ticket}"),
        ]
    )

    decision = RouteDecision.model_validate(raw_decision)

    return {"route_decision": decision}


class ClassifierState(TypedDict):
    ticket: str


class FinalState(TypedDict):
    final_response: str


def billing(state: ClassifierState) -> FinalState:
    ticket = state["ticket"]

    response = llm.invoke(
        [
            SystemMessage(
                content="You are a billing manager. Respond to user's ticket accordingly"
            ),
            HumanMessage(content=f"ticket: {ticket}"),
        ]
    )

    print("billing manager: ", str(response.content))

    return {"final_response": str(response.content)}


def technical(state: ClassifierState) -> FinalState:
    ticket = state["ticket"]

    response = llm.invoke(
        [
            SystemMessage(
                content="You are a technical manager. Respond to user's ticket accordingly"
            ),
            HumanMessage(content=f"ticket: {ticket}"),
        ]
    )

    print("technical manager: ", str(response.content))

    return {"final_response": str(response.content)}


def account(state: ClassifierState) -> FinalState:
    ticket = state["ticket"]

    response = llm.invoke(
        [
            SystemMessage(
                content="You are a account manager. Respond to user's ticket accordingly"
            ),
            HumanMessage(content=f"ticket: {ticket}"),
        ]
    )

    print("account manager: ", str(response.content))

    return {"final_response": str(response.content)}


def general(state: ClassifierState) -> FinalState:
    ticket = state["ticket"]

    response = llm.invoke(
        [
            SystemMessage(
                content="You are a general assistant. Respond to user's ticket accordingly"
            ),
            HumanMessage(content=f"ticket: {ticket}"),
        ]
    )

    print("general: ", str(response.content))

    return {"final_response": str(response.content)}


builder = StateGraph(SupportTicketRouterState)
builder.add_node("classifier", classifier)
builder.add_node("billing", billing)
builder.add_node("technical", technical)
builder.add_node("account", account)
builder.add_node("general", general)

builder.add_edge(START, "classifier")


class DecisionState(TypedDict):
    route_decision: RouteDecision


def decide(state: DecisionState):
    return state["route_decision"].route


builder.add_conditional_edges("classifier", decide)
builder.add_edge("billing", END)
builder.add_edge("technical", END)
builder.add_edge("account", END)
builder.add_edge("general", END)
graph = builder.compile()

RESPONSE_NODES = {"billing", "technical", "account", "general"}


def respond(user_input: str) -> str:
    chunks: list[str] = []

    for message_chunk, metadata in graph.stream(
        {"ticket": user_input},
        stream_mode="messages",
    ):
        node_name = metadata.get("langgraph_node")

        # Skip classifier tokens; only stream the final response node.
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

            # print(respond(user_input))
            respond(user_input)
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
