"""Learning graph for the Editor In Chief Review Loop simulated agent.

This graph practices a human-in-the-loop review gate: draft, interrupt for
review, classify the review decision, then publish or finish without publishing.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from my_agents.settings import get_settings

AGENT_NAME = "editor_in_chief_review_loop"

settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model)


class EditorInChiefReviewState(TypedDict):
    user_request: str
    draft: NotRequired[str]
    review_decision: NotRequired[Literal["approve", "revise", "reject"]]
    human_feedback: NotRequired[str]
    revision_count: NotRequired[int]
    final_result: NotRequired[str]


def draft_writer(state: EditorInChiefReviewState) -> dict[str, str | int]:
    user_request = state["user_request"]
    human_feedback = state.get("human_feedback", "")
    revision_count = state.get("revision_count", 0)
    if not user_request:
        raise RuntimeError("User request is required")

    system_prompt = f"""
    You are draft writer. Write a rough draft based on user request.

    You may also be provided with a human feedback if there is any.

    {f"feedback: {human_feedback}" if human_feedback else ""}
    """
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"user request: {user_request}"),
        ]
    )
    print(f"draft_writer: {response.content}")

    return {
        "draft": response.content if isinstance(response.content, str) else str(response.content),
        "revision_count": (revision_count + 1) if human_feedback else 0,
    }


def human_review(state: EditorInChiefReviewState) -> dict[str, str]:
    draft = state["draft"]

    if not draft:
        raise RuntimeError("No draft has been generated. it is required")

    feedback = interrupt({"question": "What do you think about the draft?: ", "draft": draft})

    return {"human_feedback": str(feedback)}


class ReviewDecision(BaseModel):
    decision: Literal["approve", "revise", "reject"] = Field(
        description="Classify whether the reviewer approved, requested revision, or rejected."
    )


def classify_feedback(
    state: EditorInChiefReviewState,
) -> dict[str, Literal["approve", "revise", "reject"]]:
    feedback = state["human_feedback"]
    if not feedback:
        raise RuntimeError("Requires human feedback")
    response = llm.with_structured_output(ReviewDecision).invoke(
        [
            SystemMessage(
                content=(
                    "Classify the review feedback as exactly one decision. "
                    "Use approve for clear acceptance, revise for requested changes, "
                    "and reject for cancellation or refusal."
                )
            ),
            HumanMessage(content=f"Review feedback:\n{feedback}"),
        ]
    )

    print(f"classifier: {str(response)}")

    return {"review_decision": response.decision}


def publisher(state: EditorInChiefReviewState) -> dict[str, str]:
    user_request = state["user_request"]
    draft = state["draft"]
    decision = state["review_decision"]

    if not user_request or not draft or not decision:
        raise RuntimeError("no required args")
    elif decision != "approve":
        raise RuntimeError("decision not approved")

    system_prompt = f"""
    You are a publisher. Here is the draft that received final approval.

    Elaborate it.

    Draft: {draft}
    """

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"User request: {user_request}"),
        ]
    )

    print(f"publisher: {response.content}")

    return {
        "final_result": response.content
        if isinstance(response.content, str)
        else str(response.content)
    }


def finish_without_publish(state: EditorInChiefReviewState) -> dict[str, str]:
    """Finalize rejected or max-revision paths with an explicit terminal result.

    This prevents terminal states that lack `final_result`, which is what caused
    the CLI boundary to fail with KeyError when it assumed every END path had
    passed through `publisher`.
    """

    decision = state["review_decision"]
    draft = state.get("draft", "")
    revision_count = state.get("revision_count", 0)
    reason = (
        "The reviewer rejected the draft."
        if decision == "reject"
        else "The draft reached the maximum revision count before approval."
    )
    return {
        "final_result": (
            f"{reason}\n\n"
            "No publish side effect was performed.\n\n"
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


def _router(
    state: EditorInChiefReviewState,
) -> Literal["publisher", "draft_writer", "finish_without_publish"]:
    decision: Literal["approve", "revise", "reject"] = state["review_decision"]
    revision_count = state["revision_count"]
    if not decision:
        raise RuntimeError("no decision. it is required")
    if decision == "approve":
        return "publisher"
    if decision == "reject":
        return "finish_without_publish"
    if revision_count >= 2:
        return "finish_without_publish"
    return "draft_writer"


builder.add_conditional_edges(
    "classify_feedback",
    _router,
    {
        "publisher": "publisher",
        "draft_writer": "draft_writer",
        "finish_without_publish": "finish_without_publish",
    },
)
builder.add_edge("publisher", END)
builder.add_edge("finish_without_publish", END)

config: RunnableConfig = {"configurable": {"thread_id": "1"}}
graph = builder.compile(checkpointer=InMemorySaver())


def respond(user_input: str) -> str:
    """Thin CLI adapter around the graph invocation."""
    response = graph.invoke({"user_request": user_input}, config=config)

    if "__interrupt__" in response:
        return "Review interrupt reached. Resume the graph with review feedback to finish."

    return response["final_result"]


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
