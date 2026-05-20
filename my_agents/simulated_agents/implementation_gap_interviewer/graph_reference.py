"""Reference implementation for the Implementation Gap Interviewer simulated agent.

This file is a comparison artifact, not a replacement for ``graph.py``. It keeps
LLM-backed semantic extraction while making state updates, structured-output
schemas, interrupt/resume flow, and route decisions explicit for learning.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from my_agents.settings import get_settings

AGENT_NAME = "implementation_gap_interviewer_reference"
MAX_QUESTIONS = 3

TargetArea = Literal[
    "fastapi_endpoint",
    "conversation_streaming",
    "rag_service",
    "langgraph_interrupt",
    "deployment",
    "unknown",
]
CurrentMode = Literal[
    "reviewing_agent_built_code",
    "can_modify_existing_code",
    "cannot_rebuild_from_scratch",
    "has_rebuilt_small_version",
    "unknown",
]
EvidenceLevel = Literal["none", "watched", "reviewed", "modified", "rebuilt_from_scratch"]
NextNode = Literal["ask_gap_question", "recommend_practice_slice", "recommend_from_incomplete"]

settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model, reasoning_effort="low")


class ImplementationGapInterviewState(TypedDict):
    """Shared state for the implementation-gap interview graph.

    Only ``user_request`` is required at graph start. The remaining fields are
    produced by graph nodes as the interview extracts context, asks for missing
    evidence, resumes, and recommends a practice slice.
    """

    user_request: str
    learner_context: NotRequired[dict[str, str | list[str]]]
    target_area: NotRequired[str]
    required_axes: NotRequired[list[str]]
    ownership_evidence: NotRequired[dict[str, str]]
    missing_axes: NotRequired[list[str]]
    blockers: NotRequired[list[str]]
    next_question: NotRequired[str]
    last_answer: NotRequired[str]
    question_count: NotRequired[int]
    ready_to_recommend: NotRequired[bool]
    final_result: NotRequired[str]


class LearnerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_area: TargetArea = "unknown"
    current_mode: list[CurrentMode] = Field(default_factory=list)
    notes: str = ""


class RequiredAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    axes: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    axis: str
    level: EvidenceLevel


class EvidenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: list[EvidenceItem] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class PracticeSlice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    goal: str
    success_criteria: list[str] = Field(default_factory=list)
    allowed_help: list[str] = Field(default_factory=list)
    stop_condition: str


def collect_learning_context(state: ImplementationGapInterviewState) -> dict[str, object]:
    print("[collect_learning_context] extracting learner context")
    user_request = state["user_request"]
    context = llm.with_structured_output(LearnerContext, method="function_calling").invoke(
        [
            SystemMessage(
                content=(
                    "Extract only explicit learning context from the learner's message. "
                    "Do not infer confidence. Use unknown when unclear."
                )
            ),
            HumanMessage(content=f"Learner message:\n{user_request}"),
        ]
    )
    parsed = LearnerContext.model_validate(context)
    return {
        "learner_context": parsed.model_dump(),
        "target_area": parsed.target_area,
        "question_count": 0,
        "ownership_evidence": {},
        "blockers": [],
    }


def map_implementation_axes(state: ImplementationGapInterviewState) -> dict[str, object]:
    print("[map_implementation_axes] mapping required implementation axes")
    user_request = state["user_request"]
    learner_context = state["learner_context"]
    axes = llm.with_structured_output(RequiredAxes, method="function_calling").invoke(
        [
            SystemMessage(
                content=(
                    "Choose 3 to 6 concrete implementation axes the learner would need to own. "
                    "Use short snake_case names. Do not include broad personality traits."
                )
            ),
            HumanMessage(
                content=(
                    f"Learner message:\n{user_request}\n\n"
                    f"Extracted learner context:\n{learner_context}"
                )
            ),
        ]
    )
    parsed = RequiredAxes.model_validate(axes)
    required_axes = parsed.axes or ["minimal_route", "state_contract", "test"]
    return {"required_axes": required_axes}


def validate_implementation_ownership(
    state: ImplementationGapInterviewState,
) -> dict[str, object]:
    print("[validate_implementation_ownership] checking evidence gaps")
    required_axes = state["required_axes"]
    ownership_evidence = dict(state.get("ownership_evidence", {}))

    # Let the LLM extract only evidence from the available conversation state.
    # Python still computes the missing axes and route flag below.
    evidence_update = llm.with_structured_output(EvidenceUpdate, method="function_calling").invoke(
        [
            SystemMessage(
                content=(
                    "Extract ownership evidence from the learner's original request "
                    "and latest answer. Evidence levels must be one of: none, "
                    "watched, reviewed, modified, "
                    "rebuilt_from_scratch. Do not invent evidence."
                )
            ),
            HumanMessage(
                content=(
                    f"Required axes:\n{required_axes}\n\n"
                    f"Original request:\n{state['user_request']}\n\n"
                    f"Latest answer, if any:\n{state.get('last_answer', '')}\n\n"
                    f"Existing evidence:\n{ownership_evidence}"
                )
            ),
        ]
    )
    parsed_update = EvidenceUpdate.model_validate(evidence_update)
    for item in parsed_update.evidence:
        if item.axis in required_axes:
            ownership_evidence[item.axis] = item.level

    weak_levels = {"none", "watched", "reviewed"}
    missing_axes = [
        axis for axis in required_axes if ownership_evidence.get(axis, "none") in weak_levels
    ]
    ready_to_recommend = bool(ownership_evidence) and (
        len(missing_axes) <= 2 or state.get("question_count", 0) > 0
    )

    return {
        "ownership_evidence": ownership_evidence,
        "missing_axes": missing_axes,
        "blockers": [*state.get("blockers", []), *parsed_update.blockers],
        "ready_to_recommend": ready_to_recommend,
        "next_question": build_next_question(missing_axes, ownership_evidence),
    }


def build_next_question(missing_axes: list[str], evidence: dict[str, str]) -> str:
    if not missing_axes:
        return ""
    highest_priority = missing_axes[:3]
    return (
        "For these axes, what have you personally done so far: "
        f"{', '.join(highest_priority)}? "
        "Answer with evidence levels like `axis=none|watched|reviewed|modified|"
        "rebuilt_from_scratch` and include blockers if useful. "
        f"Current evidence: {evidence}"
    )


def route_after_validation(state: ImplementationGapInterviewState) -> NextNode:
    print("[route] deciding next node")
    if state.get("ready_to_recommend", False):
        return "recommend_practice_slice"
    if state.get("question_count", 0) >= MAX_QUESTIONS:
        return "recommend_from_incomplete"
    return "ask_gap_question"


def ask_gap_question(state: ImplementationGapInterviewState) -> dict[str, object]:
    print("[ask_gap_question] interrupting for missing evidence")
    answer = interrupt(
        {
            "type": "implementation_gap_required",
            "question": state["next_question"],
            "missing_axes": state["missing_axes"],
            "current_evidence": state.get("ownership_evidence", {}),
            "answer_format": "axis=evidence_level; blocker=...",
        }
    )
    return {
        "last_answer": str(answer),
        "question_count": state.get("question_count", 0) + 1,
    }


def update_evidence(state: ImplementationGapInterviewState) -> dict[str, object]:
    """Keep this node as the explicit resume-answer update boundary.

    The reference implementation performs extraction in validation so Python can
    compute missing axes in one place. This node still records the resume value
    boundary and exists to make the graph shape match the learner README.
    """

    print("[update_evidence] resume answer is ready for validation")
    return {}


def recommend_practice_slice(state: ImplementationGapInterviewState) -> dict[str, str]:
    print("[recommend_practice_slice] creating one small exercise")
    suggestion = llm.with_structured_output(PracticeSlice, method="function_calling").invoke(
        [
            SystemMessage(
                content=(
                    "Recommend exactly one small solo implementation exercise. "
                    "This is not career advice. The exercise should be doable without AI writing "
                    "the first implementation. Include concrete success criteria."
                )
            ),
            HumanMessage(
                content=(
                    f"Target area: {state['target_area']}\n"
                    f"Required axes: {state['required_axes']}\n"
                    f"Ownership evidence: {state.get('ownership_evidence', {})}\n"
                    f"Missing axes: {state.get('missing_axes', [])}\n"
                    f"Blockers: {state.get('blockers', [])}\n"
                    f"Original request: {state['user_request']}"
                )
            ),
        ]
    )
    parsed = PracticeSlice.model_validate(suggestion)
    criteria = "\n".join(f"- {item}" for item in parsed.success_criteria)
    allowed = "\n".join(f"- {item}" for item in parsed.allowed_help)
    return {
        "final_result": (
            f"Next solo practice slice: {parsed.title}\n\n"
            f"Goal:\n{parsed.goal}\n\n"
            f"Success criteria:\n{criteria}\n\n"
            f"Allowed help:\n{allowed}\n\n"
            f"Stop condition:\n{parsed.stop_condition}"
        )
    }


def recommend_from_incomplete(state: ImplementationGapInterviewState) -> dict[str, str]:
    print("[recommend_from_incomplete] choosing safest small exercise")
    target_area = state.get("target_area", "unknown")
    missing_axes = state.get("missing_axes", [])
    return {
        "final_result": (
            "Not enough evidence was collected, so start with the smallest safe exercise.\n\n"
            f"Target area: {target_area}\n"
            f"Still unclear axes: {missing_axes}\n\n"
            "Practice slice: create a tiny script or endpoint that proves one axis only. "
            "After your first working attempt, ask for code review instead of implementation."
        )
    }


graph_builder = StateGraph(ImplementationGapInterviewState)
graph_builder.add_node("collect_learning_context", collect_learning_context)
graph_builder.add_node("map_implementation_axes", map_implementation_axes)
graph_builder.add_node("validate_implementation_ownership", validate_implementation_ownership)
graph_builder.add_node("ask_gap_question", ask_gap_question)
graph_builder.add_node("update_evidence", update_evidence)
graph_builder.add_node("recommend_practice_slice", recommend_practice_slice)
graph_builder.add_node("recommend_from_incomplete", recommend_from_incomplete)

graph_builder.add_edge(START, "collect_learning_context")
graph_builder.add_edge("collect_learning_context", "map_implementation_axes")
graph_builder.add_edge("map_implementation_axes", "validate_implementation_ownership")
graph_builder.add_conditional_edges(
    "validate_implementation_ownership",
    route_after_validation,
    {
        "ask_gap_question": "ask_gap_question",
        "recommend_practice_slice": "recommend_practice_slice",
        "recommend_from_incomplete": "recommend_from_incomplete",
    },
)
graph_builder.add_edge("ask_gap_question", "update_evidence")
graph_builder.add_edge("update_evidence", "validate_implementation_ownership")
graph_builder.add_edge("recommend_practice_slice", END)
graph_builder.add_edge("recommend_from_incomplete", END)

graph = graph_builder.compile(checkpointer=InMemorySaver())


def respond(user_input: str) -> str:
    """Thin CLI adapter for invoking and resuming the graph."""

    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    state = graph.invoke({"user_request": user_input}, config=config)

    while "__interrupt__" in state:
        interrupt_payload = state["__interrupt__"][0].value
        print("\n[interrupt payload]")
        print(f"Missing axes: {interrupt_payload['missing_axes']}")
        print(f"Current evidence: {interrupt_payload['current_evidence']}")
        answer = input(f"AI: {interrupt_payload['question']}\n🧑‍💻 Answer: ")
        state = graph.invoke(Command(resume=answer), config=config)

    return str(state["final_result"])


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
