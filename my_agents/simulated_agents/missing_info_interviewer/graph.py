"""Missing Info Interviewer simulated agent.

This graph practices LangGraph interrupt/resume for collecting missing required
fields. It intentionally uses deterministic parsing so the architecture is easy
to see before adding LLM extraction later.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

AGENT_NAME = "missing_info_interviewer"
REQUIRED_FIELDS = ("goal", "audience", "deliverable", "deadline")
MAX_QUESTIONS = 3

NextNode = Literal["ask_missing_info", "summarize_request", "finish_incomplete"]


class MissingInfoInterviewState(TypedDict):
    """Shared state for the full missing-info interview graph.

    Only ``user_request`` is required at graph start. Every other field is
    produced by nodes as the graph validates, interrupts, resumes, and merges.
    """

    user_request: str
    collected_info: NotRequired[dict[str, str]]
    missing_fields: NotRequired[list[str]]
    next_question: NotRequired[str]
    last_answer: NotRequired[str]
    question_count: NotRequired[int]
    ready_to_summarize: NotRequired[bool]
    final_result: NotRequired[str]


class TerminalResult(BaseModel):
    """Completed graph output contract."""

    final_result: str


def parse_key_value_text(text: str) -> dict[str, str]:
    """Parse simple practice input like ``goal=prep; audience=team``.

    This fake parser is intentionally small. It keeps the learning focus on the
    graph architecture rather than LLM extraction quality.
    """

    parsed: dict[str, str] = {}
    for raw_part in text.split(";"):
        if "=" not in raw_part:
            continue
        raw_key, raw_value = raw_part.split("=", 1)
        key = raw_key.strip().lower()
        value = raw_value.strip()
        if key in REQUIRED_FIELDS and value:
            parsed[key] = value
    return parsed


def collect_request(state: MissingInfoInterviewState) -> dict[str, dict[str, str] | int]:
    print("[collect_request] extracting known fields")
    user_request = state["user_request"]
    collected_info = parse_key_value_text(user_request)

    # Tiny deterministic convenience so the example sentence is not totally
    # empty. Keep this conservative: do not invent missing fields.
    if "tomorrow" in user_request.lower() and "deadline" not in collected_info:
        collected_info["deadline"] = "tomorrow"

    return {"collected_info": collected_info, "question_count": 0}


def validate_required_fields(
    state: MissingInfoInterviewState,
) -> dict[str, list[str] | str | bool]:
    print("[validate_required_fields] checking missing fields")
    collected_info = state.get("collected_info", {})
    missing_fields = [field for field in REQUIRED_FIELDS if not collected_info.get(field)]

    if not missing_fields:
        return {
            "missing_fields": [],
            "next_question": "",
            "ready_to_summarize": True,
        }

    return {
        "missing_fields": missing_fields,
        "next_question": build_next_question(missing_fields),
        "ready_to_summarize": False,
    }


def build_next_question(missing_fields: list[str]) -> str:
    field_help = {
        "goal": "What is the goal of this request?",
        "audience": "Who is this for?",
        "deliverable": "What deliverable should be produced?",
        "deadline": "When is it needed?",
    }
    questions = [field_help[field] for field in missing_fields]
    return " ".join(questions) + " Reply as key=value pairs separated by semicolons."


def route_after_validation(state: MissingInfoInterviewState) -> NextNode:
    print("[route] deciding next node")
    if state.get("ready_to_summarize", False):
        return "summarize_request"
    if state.get("question_count", 0) >= MAX_QUESTIONS:
        return "finish_incomplete"
    return "ask_missing_info"


def ask_missing_info(state: MissingInfoInterviewState) -> dict[str, str | int]:
    answer = interrupt(
        {
            "type": "missing_info_required",
            "question": state["next_question"],
            "missing_fields": state["missing_fields"],
            "current_info": state.get("collected_info", {}),
            "answer_format": "goal=...; audience=...; deliverable=...; deadline=...",
        }
    )
    print("[ask_missing_info] received resumed answer")
    return {
        "last_answer": str(answer),
        "question_count": state.get("question_count", 0) + 1,
    }


def merge_answer(state: MissingInfoInterviewState) -> dict[str, dict[str, str]]:
    print("[merge_answer] merging resume value into state")
    collected_info = dict(state.get("collected_info", {}))
    answer_info = parse_key_value_text(state["last_answer"])
    collected_info.update(answer_info)
    return {"collected_info": collected_info}


def summarize_request(state: MissingInfoInterviewState) -> dict[str, str]:
    print("[summarize_request] writing final result")
    info = state["collected_info"]
    return {
        "final_result": (
            "Ready to act on this clarified request:\n"
            f"- Goal: {info['goal']}\n"
            f"- Audience: {info['audience']}\n"
            f"- Deliverable: {info['deliverable']}\n"
            f"- Deadline: {info['deadline']}"
        )
    }


def finish_incomplete(state: MissingInfoInterviewState) -> dict[str, str]:
    print("[finish_incomplete] ending with explicit incomplete result")
    collected_info = state.get("collected_info", {})
    missing_fields = state.get("missing_fields", [])
    return {
        "final_result": (
            "Could not fully clarify the request within the question limit.\n"
            f"Known info: {collected_info}\n"
            f"Still missing: {missing_fields}"
        )
    }


builder = StateGraph(MissingInfoInterviewState)
builder.add_node("collect_request", collect_request)
builder.add_node("validate_required_fields", validate_required_fields)
builder.add_node("ask_missing_info", ask_missing_info)
builder.add_node("merge_answer", merge_answer)
builder.add_node("summarize_request", summarize_request)
builder.add_node("finish_incomplete", finish_incomplete)

builder.add_edge(START, "collect_request")
builder.add_edge("collect_request", "validate_required_fields")
builder.add_conditional_edges(
    "validate_required_fields",
    route_after_validation,
    {
        "ask_missing_info": "ask_missing_info",
        "summarize_request": "summarize_request",
        "finish_incomplete": "finish_incomplete",
    },
)
builder.add_edge("ask_missing_info", "merge_answer")
builder.add_edge("merge_answer", "validate_required_fields")
builder.add_edge("summarize_request", END)
builder.add_edge("finish_incomplete", END)

graph = builder.compile(checkpointer=InMemorySaver())


def respond(user_input: str) -> str:
    """Thin CLI adapter that invokes and resumes the interrupting graph."""

    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    state = graph.invoke({"user_request": user_input}, config=config)

    while "__interrupt__" in state:
        interrupt_payload = state["__interrupt__"][0].value
        print("\n[interrupt payload]")
        print(f"Question: {interrupt_payload['question']}")
        print(f"Missing fields: {interrupt_payload['missing_fields']}")
        print(f"Current info: {interrupt_payload['current_info']}")
        print(f"Expected format: {interrupt_payload['answer_format']}")
        answer = input("🧑‍🏫 Answer: ")
        state = graph.invoke(Command(resume=answer), config=config)

    return TerminalResult.model_validate(state).final_result


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
