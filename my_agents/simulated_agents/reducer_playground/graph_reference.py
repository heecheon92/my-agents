"""Reference implementation for the Reducer Playground simulated agent.

This file is intentionally deterministic and credential-free because the learning
point is reducer behavior, not model quality.

The key difference from the user implementation is explicit state separation:

- `ReducerPlaygroundInput` is what callers provide.
- `ReducerPlaygroundState` is the internal graph state, including reducer-backed
  `notes`.
- `ReducerPlaygroundOutput` is the narrow result callers should read.

It also annotates node return updates so static type checkers can catch mistakes
such as returning `str` to a reducer channel that expects `list[str]`.
"""

from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph


class ReducerPlaygroundInput(TypedDict):
    """Public input state: the caller only needs to provide the question."""

    question: str


class ReducerPlaygroundState(TypedDict):
    """Internal graph state.

    `notes` is reducer-backed because both parallel worker nodes update it.
    Every node update for `notes` must be a list, for example:

    `{"notes": ["one note"]}`

    Returning `{"notes": "one note"}` is a type-shape bug because
    `operator.add` would try to concatenate `list[str] + str`.
    """

    question: str
    notes: Annotated[list[str], operator.add]
    final_summary: NotRequired[str]


class ReducerPlaygroundOutput(TypedDict):
    """Public output state: callers only need the final summary."""

    final_summary: str


class NotesUpdate(TypedDict):
    """Reducer-safe update returned by parallel worker nodes."""

    notes: list[str]


class SummaryUpdate(TypedDict):
    """Final update returned by the synthesizer node."""

    final_summary: str


def backend_dev(state: ReducerPlaygroundState) -> NotesUpdate:
    """Return one backend-focused note as a list so the reducer can merge it."""
    question = state["question"]
    return {
        "notes": [
            "Backend perspective: define the API contract first, then verify it with "
            f"offline tests before adding model behavior. Topic: {question}"
        ]
    }


def architect(state: ReducerPlaygroundState) -> NotesUpdate:
    """Return one architecture-focused note as a list so the reducer can merge it."""
    question = state["question"]
    return {
        "notes": [
            "Architecture perspective: keep state channels explicit and make merge rules "
            f"visible before adding more graph branches. Topic: {question}"
        ]
    }


def synthesizer(state: ReducerPlaygroundState) -> SummaryUpdate:
    """Read merged notes and produce the final user-facing summary."""
    question = state["question"]
    notes = state["notes"]
    bullet_list = "\n".join(f"- {note}" for note in notes)
    return {
        "final_summary": (
            f"Reducer Playground summary for: {question}\n\n"
            "Merged notes:\n"
            f"{bullet_list}\n\n"
            "Learning point: because `notes` uses `Annotated[list[str], operator.add]`, "
            "each worker returns a one-item list and LangGraph combines those lists."
        )
    }


builder = StateGraph(
    ReducerPlaygroundState,
    input_schema=ReducerPlaygroundInput,
    output_schema=ReducerPlaygroundOutput,
)
builder.add_node("architect", architect)
builder.add_node("backend_dev", backend_dev)
builder.add_node("synthesizer", synthesizer)

builder.add_edge(START, "architect")
builder.add_edge(START, "backend_dev")
builder.add_edge("architect", "synthesizer")
builder.add_edge("backend_dev", "synthesizer")
builder.add_edge("synthesizer", END)

graph = builder.compile()


if __name__ == "__main__":
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            initial_state: ReducerPlaygroundInput = {"question": user_input}
            result = graph.invoke(initial_state)

            print("\n[final answer]")
            print(result["final_summary"])
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
