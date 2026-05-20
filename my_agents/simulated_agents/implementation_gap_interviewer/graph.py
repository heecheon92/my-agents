"""Bootstrap graph module for the Implementation Gap Interviewer simulated agent.

This file intentionally starts with only a terminal loop. Replace `respond()` with
LangGraph state, nodes, routing, and graph invocation after the learning pattern is
clear.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from my_agents.settings import get_settings

AGENT_NAME = "implementation_gap_interviewer"


def node(fn):
    return fn


edge = node

settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model)


class ImplementationGapInterviewState(TypedDict):
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


GS = ImplementationGapInterviewState


class PGS(GS):
    user_request: NotRequired[str]


class LearnerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_area: Literal[
        "fastapi_endpoint",
        "conversation_streaming",
        "rag_service",
        "langgraph_interrupt",
        "unknown",
    ] = "unknown"
    current_mode: list[
        Literal[
            "reviewing_agent_built_code",
            "can_modify_existing_code",
            "cannot_rebuild_from_scratch",
            "has_rebuilt_small_version",
            "unknown",
        ]
    ] = []
    notes: str = ""


@node
def collect_learning_context(state: GS) -> PGS:
    user_request = state["user_request"]
    context = llm.with_structured_output(LearnerContext).invoke(
        [
            SystemMessage(
                content=(
                    "Extract useful information about user's learning context"
                    "Do not make hard inferrence. Simply rely on user's message."
                )
            ),
            HumanMessage(content=(f"User: {user_request}")),
        ]
    )
    parsed = LearnerContext.model_validate(context)

    return {
        "learner_context": parsed.model_dump(),
    }


class RequiredAxes(BaseModel):
    axes: list[str]


@node
def map_implementation_axes(state: GS) -> PGS:
    user_request = state["user_request"]
    user_context = state["learner_context"]
    axes = llm.with_structured_output(RequiredAxes).invoke(
        [
            SystemMessage(
                content=(
                    "Infer some required axes for user to achieve his or her learning goal."
                    f"""
            Here are some extra context that would help your judgements.

            Context: {user_context}
            """
                )
            ),
            HumanMessage(content=user_request),
        ]
    )
    parsed = RequiredAxes.model_validate(axes)
    return {"target_area": user_context["target_area"], "required_axes": parsed.axes}


EvidenceLevel = Literal["none", "watched", "reviewed", "modified", "rebuilt_from_scratch"]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    axis: str
    level: EvidenceLevel


class UpdateEvidenceResult(BaseModel):
    ownership_evidence: list[EvidenceItem]


class OwnershipEvidence(BaseModel):
    ownership_evidence: list[EvidenceItem]
    missing_axes: list[str] = Field([], description="missing axes based on required axes")
    ready_to_recommend: bool = Field(
        False, description="Whether enough context gathered to recommend practice slice"
    )
    next_question: str = Field(
        "",
        description=(
            "Question to ask to gather more information. "
            "You can leave this empty when there is enough context."
        ),
    )


@node
def validate_implementation_ownership(state: GS) -> PGS:
    required_axes = state["required_axes"]
    target_area = state["target_area"]
    response = llm.with_structured_output(OwnershipEvidence).invoke(
        [
            SystemMessage(
                content=(
                    "Analyze the user context and decide whether enough information "
                    "has been gathered to provide a recommendation."
                    f"""
            Following data are user's current information that have been gathered and some criteria.
            
            ##Target area: {target_area}

            ##Required axes: {", ".join(required_axes)}
            """
                )
            )
        ]
    )
    parsed = OwnershipEvidence.model_validate(response)
    ownership_evidence = state.get("ownership_evidence", {})

    for update_result in parsed.ownership_evidence:
        ownership_evidence[update_result.axis] = update_result.level

    return {
        "ownership_evidence": ownership_evidence,
        "missing_axes": parsed.missing_axes,
        "ready_to_recommend": parsed.ready_to_recommend,
        "next_question": parsed.next_question,
    }


@node
def ask_gap_question(state: GS) -> PGS:
    question_count = state.get("question_count", 0)
    answer = interrupt(
        {
            "type": "implementation_gap_required",
            "question": state["next_question"],
            "missing_axes": state["missing_axes"],
            "current_evidence": state.get("ownership_evidence", {}),
        }
    )
    return {"last_answer": str(answer), "question_count": question_count + 1}


@node
def update_evidence(state: GS) -> PGS:
    user_request = state["user_request"]

    last_answer = state["last_answer"]
    evidence = state.get("ownership_evidence", {})
    result = llm.with_structured_output(UpdateEvidenceResult).invoke(
        [
            SystemMessage(
                content=(
                    "Update existing learning ownership evidence based on user's statements"
                    "This is an existing ownership evidence: "
                    f"{str(evidence)}"
                    "DO NOT DELETE OR DEMOTE EXISTING KEY / VALUE. (ADD OR PROMOTE ONLY)"
                )
            ),
            HumanMessage(content=f"User's initial request: {user_request}"),
            HumanMessage(content=f"User statement: {last_answer}"),
        ]
    )
    parsed = UpdateEvidenceResult.model_validate(result)
    for update_result in parsed.ownership_evidence:
        evidence[update_result.axis] = update_result.level

    return {"ownership_evidence": evidence}


@node
def recommend_practice_slice(state: GS) -> PGS:
    suggestion = llm.invoke(
        [
            SystemMessage(
                content=f"""
        The final result is not a career judgment. 
        Provide one small exercise the learner can implement.
        Following are curated user context collected via series of interview phases.
                      
        TARGET AREA: {state["target_area"]}
        OWNERSHIP EVIDENT: {state["ownership_evidence"]}
        """
            ),
            HumanMessage(content=state["user_request"]),
        ]
    )

    return {"final_result": suggestion.content}


@node
def recommend_from_incomplete(state: GS) -> PGS:
    return {"final_result": "I cannot recommend without more information about you."}


@edge
def route(state: GS) -> str:
    question_count = state.get("question_count", 0)
    if question_count >= 3:
        return "recommend_from_incomplete"

    ready_to_recommend = state.get("ready_to_recommend", False)
    if ready_to_recommend:
        return "recommend_practice_slice"
    return "ask_gap_question"


builder = StateGraph(GS)
builder.add_node("collect_learning_context", collect_learning_context)
builder.add_node("map_implementation_axes", map_implementation_axes)
builder.add_node("validate_implementation_ownership", validate_implementation_ownership)
builder.add_node("ask_gap_question", ask_gap_question)
builder.add_node("update_evidence", update_evidence)
builder.add_node("recommend_practice_slice", recommend_practice_slice)
builder.add_node("recommend_from_incomplete", recommend_from_incomplete)

builder.add_edge(START, "collect_learning_context")
builder.add_edge("collect_learning_context", "map_implementation_axes")
builder.add_edge("map_implementation_axes", "validate_implementation_ownership")
builder.add_conditional_edges(
    "validate_implementation_ownership",
    route,
    {
        "recommend_practice_slice": "recommend_practice_slice",
        "recommend_from_incomplete": "recommend_from_incomplete",
        "ask_gap_question": "ask_gap_question",
    },
)
builder.add_edge("ask_gap_question", "update_evidence")
builder.add_edge("update_evidence", "validate_implementation_ownership")
builder.add_edge("recommend_practice_slice", END)
builder.add_edge("recommend_from_incomplete", END)

config: RunnableConfig = {"configurable": {"thread_id": "1"}}

graph = builder.compile(checkpointer=InMemorySaver())


def respond(user_input: str) -> str:
    state = graph.invoke({"user_request": user_input}, config=config)

    while "__interrupt__" in state:
        interrupt_payload = state["__interrupt__"][0].value
        answer = input(f"AI: {interrupt_payload['question']}")
        state = graph.invoke(Command(resume=answer), config=config)

    return state["final_result"]


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
