"""Reference implementation for the Editor In Chief Review Loop simulation.

This file keeps the same learning pattern as ``graph.py`` but makes terminal
state handling explicit: every completed route writes ``final_result`` before
END. The CLI then validates the final state instead of assuming the key exists.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from dotenv import load_dotenv
from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from my_agents.settings import get_settings

load_dotenv()
settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model)

ReviewRoute = Literal["approve", "revise", "reject"]
NextNode = Literal["publisher", "draft_writer", "finish_without_publish"]
MAX_REVISIONS = 2


class EditorInChiefReviewState(TypedDict):
    """Shared notebook for the whole review workflow.

    ``user_request`` is required input. Every other field is produced by a graph
    node, so it is marked ``NotRequired``. This is the type-level reminder that
    ``state["final_result"]`` is unsafe until a terminal node has run.
    """

    user_request: str
    draft: NotRequired[str]
    human_feedback: NotRequired[str]
    review_decision: NotRequired[ReviewRoute]
    revision_count: NotRequired[int]
    final_result: NotRequired[str]


class ReviewClassification(BaseModel):
    """Structured LLM output for routing from free-form review feedback."""

    decision: ReviewRoute = Field(
        description=(
            "approve if the reviewer accepts the draft, revise if they ask for "
            "changes, reject if they cancel or refuse the draft."
        )
    )


class TerminalResult(BaseModel):
    """Runtime validation for completed graph output.

    Static types can remind us that ``final_result`` is optional in graph state,
    but LangGraph state changes dynamically. This model gives the CLI boundary a
    small runtime assertion that the graph actually reached a terminal node.
    """

    final_result: str


def _content_to_text(content: object) -> str:
    return content if isinstance(content, str) else str(content)


def draft_writer(state: EditorInChiefReviewState) -> dict[str, str | int]:
    print("[draft_writer] writing or revising draft")
    user_request = state["user_request"]
    previous_draft = state.get("draft", "")
    human_feedback = state.get("human_feedback", "")
    revision_count = state.get("revision_count", 0)

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are a simulated draft writer. Write concise portfolio copy. "
                    "If reviewer feedback is present, revise the previous draft. "
                    "Do not claim that anything was actually published."
                )
            ),
            HumanMessage(
                content=(
                    f"User request:\n{user_request}\n\n"
                    f"Previous draft:\n{previous_draft or '(none)'}\n\n"
                    f"Reviewer feedback:\n{human_feedback or '(none)'}"
                )
            ),
        ]
    )

    return {
        "draft": _content_to_text(response.content),
        "revision_count": revision_count + 1 if human_feedback else 0,
    }


def human_review(state: EditorInChiefReviewState) -> dict[str, str]:
    print("[human_review] interrupting for approve/revise/reject feedback")
    draft = state["draft"]
    feedback = interrupt(
        {
            "question": "Review the draft. Reply approve, revise with feedback, or reject.",
            "draft": draft,
        }
    )
    return {"human_feedback": str(feedback)}


def classify_feedback(state: EditorInChiefReviewState) -> dict[str, ReviewRoute]:
    print("[classify_feedback] classifying reviewer feedback")
    feedback = state["human_feedback"]
    result = llm.with_structured_output(ReviewClassification).invoke(
        [
            SystemMessage(
                content=(
                    "Classify review feedback as exactly one route. Return only "
                    "the structured schema."
                )
            ),
            HumanMessage(content=f"Review feedback:\n{feedback}"),
        ]
    )
    return {"review_decision": result.decision}


def route_after_review(state: EditorInChiefReviewState) -> NextNode:
    print("[route] deciding next node")
    decision = state["review_decision"]
    revision_count = state.get("revision_count", 0)

    if decision == "approve":
        return "publisher"
    if decision == "reject":
        return "finish_without_publish"
    if revision_count >= MAX_REVISIONS:
        return "finish_without_publish"
    return "draft_writer"


def publisher(state: EditorInChiefReviewState) -> dict[str, str]:
    print("[publisher] finalizing approved draft")
    draft = state["draft"]
    return {
        "final_result": (
            "APPROVED FINAL RESULT\n\n"
            f"{draft}\n\n"
            "Simulation note: no external publish side effect was performed."
        )
    }


def finish_without_publish(state: EditorInChiefReviewState) -> dict[str, str]:
    print("[finish_without_publish] ending without publish")
    decision = state["review_decision"]
    draft = state.get("draft", "")
    revision_count = state.get("revision_count", 0)
    reason = (
        "The reviewer rejected the draft."
        if decision == "reject"
        else f"The draft hit the max revision limit ({MAX_REVISIONS})."
    )
    return {
        "final_result": (
            "NOT PUBLISHED\n\n"
            f"Reason: {reason}\n\n"
            f"Last draft:\n{draft}\n\n"
            f"Revision count: {revision_count}"
        )
    }


builder = StateGraph(EditorInChiefReviewState)
builder.add_node("draft_writer", draft_writer)
builder.add_node("human_review", human_review)
builder.add_node("classify_feedback", classify_feedback)
builder.add_node("publisher", publisher)
builder.add_node("finish_without_publish", finish_without_publish)

builder.add_edge(START, "draft_writer")
builder.add_edge("draft_writer", "human_review")
builder.add_edge("human_review", "classify_feedback")
builder.add_conditional_edges(
    "classify_feedback",
    route_after_review,
    {
        "publisher": "publisher",
        "draft_writer": "draft_writer",
        "finish_without_publish": "finish_without_publish",
    },
)
builder.add_edge("publisher", END)
builder.add_edge("finish_without_publish", END)

graph = builder.compile(checkpointer=InMemorySaver())


def run_until_terminal(user_input: str) -> str:
    """Invoke the graph and resume interrupts until a terminal result exists."""
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    state = graph.invoke({"user_request": user_input}, config=config)

    while "__interrupt__" in state:
        interrupt_payload = state["__interrupt__"][0].value
        print(f"\nDraft for review:\n{interrupt_payload['draft']}\n")
        feedback = input("🧑‍⚖️ Review feedback: ")
        state = graph.invoke(Command(resume=feedback), config=config)

    # This is the deliberate boundary assertion. If a future route reaches END
    # without writing final_result, the error happens here with a clear message.
    return TerminalResult.model_validate(state).final_result


if __name__ == "__main__":
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break

            print(run_until_terminal(user_input))
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
