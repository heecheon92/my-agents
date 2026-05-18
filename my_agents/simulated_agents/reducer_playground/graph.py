"""Bootstrap graph module for the Reducer Playground simulated agent.

This file intentionally starts with only a terminal loop. Replace `respond()` with
LangGraph state, nodes, routing, and graph invocation after the learning pattern is
clear.
"""

from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

from langchain.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from my_agents.settings import get_settings

settings = get_settings()

llm = ChatOpenAI(model=settings.openai_model)

AGENT_NAME = "reducer_playground"


class ReducerPlaygroundState(TypedDict):
    question: str
    notes: Annotated[list[str], operator.add]
    final_summary: NotRequired[str]


def backend_dev(state: ReducerPlaygroundState):
    question = state.get("question", "")
    if not question:
        raise RuntimeError("User must provide a question")

    system_prompt = """
    You are a beckend engineer. Make notes or opinions about user's query.
    """

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=f"""
        Question: {question}
    """
            ),
        ]
    )

    print("\nBackend Dev: ", str(response.content))

    return {"notes": ["Backend Engineer's Response: " + str(response.content)]}


def architect(state: ReducerPlaygroundState):
    question = state.get("question", "")
    if not question:
        raise RuntimeError("User must provide a question")

    system_prompt = """
    You are a system architect. Make notes or opinion about user's query from
    an architect's perspective.
    """

    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=f"Question: {question}")]
    )

    print("\nArchitect: ", str(response.content))

    return {"notes": ["Architect's Response: " + str(response.content)]}


def synthesizer(state: ReducerPlaygroundState):
    question = state["question"]
    notes = state["notes"]
    notes_str = "\n\n".join(notes)

    system_prompt = f"""
    Please provide response to user with additional information made by an
    architect and a backend engineer.

    {notes_str}
    """

    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=f"User question: {question}")]
    )

    print("\nSynthesizer: ", str(response.content))

    return {"final_summary": "Synthesizer Response: " + str(response.content)}


builder = StateGraph(ReducerPlaygroundState)
builder.add_node("architect", architect)
builder.add_node("backend_dev", backend_dev)
builder.add_node("synthesizer", synthesizer)

builder.add_edge(START, "architect")
builder.add_edge(START, "backend_dev")
builder.add_edge("architect", "synthesizer")
builder.add_edge("backend_dev", "synthesizer")
builder.add_edge("synthesizer", END)

graph = builder.compile()


def respond(user_input: str) -> str:
    result = graph.invoke({"question": user_input, "notes": []})
    return result["final_summary"]


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
