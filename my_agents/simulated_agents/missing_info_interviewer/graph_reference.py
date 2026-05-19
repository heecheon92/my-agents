"""Reference implementation for Missing Info Interviewer.

This is intentionally similar to ``graph.py`` but organized around the key
architecture lesson: each node owns exactly one state transition. Use it as a
comparison artifact when practicing interrupt/resume flow design.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

RequiredField = Literal["goal", "audience", "deliverable", "deadline"]
Route = Literal["ask_missing_info", "summarize_request", "finish_incomplete"]
REQUIRED_FIELDS: tuple[RequiredField, ...] = (
    "goal",
    "audience",
    "deliverable",
    "deadline",
)
MAX_QUESTIONS = 3


class MissingInfoState(TypedDict):
    # Required input.
    user_request: str

    # Produced by collect_initial_info and merge_human_answer.
    collected_info: NotRequired[dict[RequiredField, str]]

    # Produced by validate_required_info.
    missing_fields: NotRequired[list[RequiredField]]
    next_question: NotRequired[str]
    ready_to_summarize: NotRequired[bool]

    # Produced by ask_for_missing_info after Command(resume=...).
    last_answer: NotRequired[str]
    question_count: NotRequired[int]

    # Produced by terminal nodes only.
    final_result: NotRequired[str]


class CompletedOutput(BaseModel):
    final_result: str


def parse_fields(text: str) -> dict[RequiredField, str]:
    """Parse ``field=value`` pairs for deterministic architecture practice."""

    parsed: dict[RequiredField, str] = {}
    valid_fields = set(REQUIRED_FIELDS)
    for part in text.split(";"):
        if "=" not in part:
            continue
        raw_key, raw_value = part.split("=", 1)
        key = raw_key.strip().lower()
        value = raw_value.strip()
        if key in valid_fields and value:
            parsed[key] = value
    return parsed


def collect_initial_info(state: MissingInfoState) -> dict[str, object]:
    """Initial input -> collected_info.

    This node should not decide if the request is complete and should not ask
    follow-up questions. It only extracts what is already present.
    """

    print("[collect_initial_info] extracting known fields")
    request = state["user_request"]
    collected = parse_fields(request)
    if "tomorrow" in request.lower() and "deadline" not in collected:
        collected["deadline"] = "tomorrow"
    return {"collected_info": collected, "question_count": 0}


def validate_required_info(state: MissingInfoState) -> dict[str, object]:
    """collected_info -> missing_fields + route flags."""

    print("[validate_required_info] checking required fields")
    collected = state.get("collected_info", {})
    missing: list[RequiredField] = [field for field in REQUIRED_FIELDS if not collected.get(field)]
    if not missing:
        return {
            "missing_fields": [],
            "next_question": "",
            "ready_to_summarize": True,
        }
    return {
        "missing_fields": missing,
        "next_question": make_question(missing),
        "ready_to_summarize": False,
    }


def make_question(missing: list[RequiredField]) -> str:
    labels = {
        "goal": "goal",
        "audience": "audience",
        "deliverable": "deliverable",
        "deadline": "deadline",
    }
    fields = ", ".join(labels[field] for field in missing)
    return f"Please provide: {fields}. Use field=value pairs separated by semicolons."


def choose_after_validation(state: MissingInfoState) -> Route:
    """Route only from validation outputs, not from raw user text."""

    print("[route] choosing next node")
    if state.get("ready_to_summarize", False):
        return "summarize_request"
    if state.get("question_count", 0) >= MAX_QUESTIONS:
        return "finish_incomplete"
    return "ask_missing_info"


def ask_for_missing_info(state: MissingInfoState) -> dict[str, object]:
    """Pause graph execution and return a payload for the caller to render."""

    answer = interrupt(
        {
            "kind": "missing_info_required",
            "question": state["next_question"],
            "missing_fields": state["missing_fields"],
            "current_info": state.get("collected_info", {}),
            "answer_format": "goal=...; audience=...; deliverable=...; deadline=...",
        }
    )
    print("[ask_for_missing_info] resumed with human answer")
    return {
        "last_answer": str(answer),
        "question_count": state.get("question_count", 0) + 1,
    }


def merge_human_answer(state: MissingInfoState) -> dict[str, object]:
    """collected_info + last_answer -> collected_info."""

    print("[merge_human_answer] merging answer into collected info")
    collected = dict(state.get("collected_info", {}))
    collected.update(parse_fields(state["last_answer"]))
    return {"collected_info": collected}


def summarize_request(state: MissingInfoState) -> dict[str, str]:
    """Complete collected_info -> final_result."""

    print("[summarize_request] producing completed output")
    info = state["collected_info"]
    return {
        "final_result": (
            "Clarified request:\n"
            f"- goal: {info['goal']}\n"
            f"- audience: {info['audience']}\n"
            f"- deliverable: {info['deliverable']}\n"
            f"- deadline: {info['deadline']}"
        )
    }


def finish_incomplete(state: MissingInfoState) -> dict[str, str]:
    """Partial state -> final_result.

    Even incomplete terminal paths satisfy the same output contract.
    """

    print("[finish_incomplete] producing incomplete output")
    return {
        "final_result": (
            "Request still needs clarification.\n"
            f"Known info: {state.get('collected_info', {})}\n"
            f"Missing fields: {state.get('missing_fields', [])}"
        )
    }


builder = StateGraph(MissingInfoState)
builder.add_node("collect_initial_info", collect_initial_info)
builder.add_node("validate_required_info", validate_required_info)
builder.add_node("ask_missing_info", ask_for_missing_info)
builder.add_node("merge_human_answer", merge_human_answer)
builder.add_node("summarize_request", summarize_request)
builder.add_node("finish_incomplete", finish_incomplete)

builder.add_edge(START, "collect_initial_info")
builder.add_edge("collect_initial_info", "validate_required_info")
builder.add_conditional_edges(
    "validate_required_info",
    choose_after_validation,
    {
        "ask_missing_info": "ask_missing_info",
        "summarize_request": "summarize_request",
        "finish_incomplete": "finish_incomplete",
    },
)
builder.add_edge("ask_missing_info", "merge_human_answer")
builder.add_edge("merge_human_answer", "validate_required_info")
builder.add_edge("summarize_request", END)
builder.add_edge("finish_incomplete", END)

graph = builder.compile(checkpointer=InMemorySaver())


def invoke_until_done(user_request: str) -> str:
    """CLI adapter around the graph; not part of graph business logic."""

    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    state = graph.invoke({"user_request": user_request}, config=config)
    while "__interrupt__" in state:
        payload = state["__interrupt__"][0].value
        print("\n[interrupt]")
        print(payload["question"])
        print(f"Current info: {payload['current_info']}")
        print(f"Expected format: {payload['answer_format']}")
        answer = input("Answer: ")
        state = graph.invoke(Command(resume=answer), config=config)
    return CompletedOutput.model_validate(state).final_result


if __name__ == "__main__":
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                break
            print(invoke_until_done(user_input))
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
