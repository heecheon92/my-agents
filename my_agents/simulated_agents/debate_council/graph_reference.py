"""Reference implementation for the Debate Council simulated agent.

This version intentionally keeps the graph small, explicit, and readable while
showing a more production-shaped state contract:

- the user question is required input state;
- role outputs are optional because graph nodes create them over time;
- state stores clean strings instead of provider message objects;
- the CLI invokes the graph directly instead of hiding it behind `respond()`.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from dotenv import load_dotenv
from langchain.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from my_agents.settings import get_settings

load_dotenv()

settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model, reasoning_effort="low")


class DebateCouncilState(TypedDict):
    """State accumulated by the sequential debate council graph.

    `question` is the only required input. Every other field is produced by one
    node and consumed by later nodes.
    """

    question: str
    architect_response: NotRequired[str]
    builder_response: NotRequired[str]
    skeptic_response: NotRequired[str]
    final_summary: NotRequired[str]


def architect(state: DebateCouncilState) -> dict[str, str]:
    """Create the long-term architecture perspective."""
    question = state["question"]

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are the Architect in a simulated debate council. "
                    "Give a concise long-term system design perspective. "
                    "Focus on architecture, boundaries, extensibility, and maintainability."
                )
            ),
            HumanMessage(content=f"Question:\n{question}"),
        ]
    )

    content = response.content if isinstance(response.content, str) else str(response.content)
    print(f"\n[architect]\n{content}")
    return {"architect_response": content}


def builder(state: DebateCouncilState) -> dict[str, str]:
    """Create the smallest practical implementation perspective."""
    question = state["question"]
    architect_response = state["architect_response"]

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are the Practical Builder in a simulated debate council. "
                    "Recommend the smallest useful implementation step. "
                    "Prefer concrete next actions over broad architecture."
                )
            ),
            HumanMessage(
                content=(f"Question:\n{question}\n\nArchitect perspective:\n{architect_response}")
            ),
        ]
    )

    content = response.content if isinstance(response.content, str) else str(response.content)
    print(f"\n[builder]\n{content}")
    return {"builder_response": content}


def skeptic(state: DebateCouncilState) -> dict[str, str]:
    """Create the risk and counterargument perspective."""
    question = state["question"]
    architect_response = state["architect_response"]
    builder_response = state["builder_response"]

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are the Skeptic in a simulated debate council. "
                    "Point out risks, missing constraints, overengineering, and failure modes. "
                    "Be constructive, not dismissive."
                )
            ),
            HumanMessage(
                content=(
                    f"Question:\n{question}\n\n"
                    f"Architect perspective:\n{architect_response}\n\n"
                    f"Builder perspective:\n{builder_response}"
                )
            ),
        ]
    )

    content = response.content if isinstance(response.content, str) else str(response.content)
    print(f"\n[skeptic]\n{content}")
    return {"skeptic_response": content}


def moderator(state: DebateCouncilState) -> dict[str, str]:
    """Synthesize the council into one user-facing answer."""
    question = state["question"]
    architect_response = state["architect_response"]
    builder_response = state["builder_response"]
    skeptic_response = state["skeptic_response"]

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are the Moderator of a simulated debate council. "
                    "Synthesize the perspectives into one balanced recommendation. "
                    "Keep the answer practical and explicit about tradeoffs."
                )
            ),
            HumanMessage(
                content=(
                    f"Question:\n{question}\n\n"
                    f"Architect perspective:\n{architect_response}\n\n"
                    f"Builder perspective:\n{builder_response}\n\n"
                    f"Skeptic perspective:\n{skeptic_response}"
                )
            ),
        ]
    )

    content = response.content if isinstance(response.content, str) else str(response.content)
    print(f"\n[moderator]\n{content}")
    return {"final_summary": content}


graph_builder = StateGraph(DebateCouncilState)
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


if __name__ == "__main__":
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            initial_state: DebateCouncilState = {"question": user_input}
            result = graph.invoke(initial_state)

            print("\n[final answer]")
            print(result["final_summary"])
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
