"""Bootstrap graph module for the Debate Council simulated agent.

This file intentionally starts with only a terminal loop. Replace `respond()` with
LangGraph state, nodes, routing, and graph invocation after the learning pattern is
clear.
"""

from __future__ import annotations

from typing import TypedDict

from langchain.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from my_agents.settings import get_settings

settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model)
AGENT_NAME = "debate_council"


class CouncilState(TypedDict, total=False):
    question: str
    architect_response: AIMessage
    builder_response: AIMessage
    skeptic_response: AIMessage
    final_summary: AIMessage


def architect(state: CouncilState):
    question = state.get("question", "")
    if not question:
        return {}
    system_prompt = f"""
    You are an architect of a system.
    User wants your opinion about long-term structure and design perspective.

    USER_QUESTION: {question}
    """
    response = llm.invoke([SystemMessage(content=system_prompt)])

    print(f"\nArchitect: {response.content}")

    return {"architect_response": response}


def builder(state: CouncilState):
    question = state.get("question", "")
    architect_response = state.get("architect_response", "")
    if not question or not architect_response:
        return {}

    system_prompt = f"""
    We need your opinion from the smallest practical implementation perspective.
    Here are user's question and an architect's perspective.

    USER_QUESTION: {question}

    ARCHITECT_RESPONSE: {architect_response}
    """

    response = llm.invoke([SystemMessage(content=system_prompt)])

    print(f"\nBuilder: {response.content}")

    return {"builder_response": response}


def skeptic(state: CouncilState):
    question = state["question"]
    architect_response = state["architect_response"]
    builder_response = state["builder_response"]

    if not question or not architect_response or not builder_response:
        return {}

    system_prompt = f"""
    I need an opinion from a skeptic's perspective.
    Point out counterpoint about risks, over-abstraction, and missing constraints.
    Following are user's question with architect and builder's responses based on the user question.

    USER_QUESTION: {question}

    ARCHITECT_RESPONSE: {architect_response}

    BUILDER_RESPONSE: {builder_response}
    """

    response = llm.invoke([SystemMessage(content=system_prompt)])

    print(f"\nSkeptic: {response.content}")

    return {"skeptic_response": response}


def moderator(state: CouncilState):
    question = state.get("question", "")
    architect_response = state.get("architect_response", "")
    builder_response = state.get("builder_response", "")
    skeptic_response = state.get("skeptic_response", "")

    if not question or not architect_response or not builder_response or not skeptic_response:
        return {}

    system_prompt = f"""
    You are the final moderator of response of different agents about user's question.
    Make the final verdict or response to the user.
    Here are the materials you need to reference.

    USER_QUESTION: {question}

    ARCHITECT_RESPONSE: {architect_response}

    BUILDER_RESPONSE: {builder_response}

    SKEPTIC_RESPONSE: {skeptic_response}
    """

    response = llm.invoke([SystemMessage(content=system_prompt)])

    print(f"\nModerator: {response.content}")

    return {"final_summary": response}


graph_builder = StateGraph(CouncilState)
graph_builder.add_node("architect", architect)
graph_builder.add_node("builder", builder)
graph_builder.add_node("skeptic", skeptic)
graph_builder.add_node("moderator", moderator)

graph_builder.add_edge(START, "architect")
graph_builder.add_edge("architect", "builder")
graph_builder.add_edge("builder", "skeptic")
graph_builder.add_edge("skeptic", "moderator")
graph_builder.add_edge("moderator", END)

graph = graph_builder.compile()


def respond(user_input: str) -> str:
    """Return a placeholder response until the LangGraph pattern is implemented."""

    result = graph.invoke({"question": user_input})
    final_summary = result.get("final_summary")

    if final_summary is None:
        return "No final summary was generated."

    return final_summary.content


if __name__ == "__main__":
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            print(respond(user_input))
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
