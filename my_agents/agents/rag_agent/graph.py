"""LangGraph form for the RAG Agent contract surface.

The graph intentionally stays thin: it makes the RAG Agent workflow explicit as
planner -> verifier control flow without moving ContextForge retrieval,
authorization, ingestion, persistence, or provider calls into this package.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TypedDict, cast

from langgraph.graph import END, START, StateGraph

from my_agents.agents.rag_agent.contracts import (
    RagAgentVerification,
    RagAgentWorkflowPlan,
)
from my_agents.agents.rag_agent.planner import DeterministicRagAgentPlanner
from my_agents.agents.rag_agent.verifier import DeterministicRagAgentVerifier
from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute


class RagAgentGraphState(TypedDict, total=False):
    """State for the deterministic RAG Agent contract graph."""

    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    resolved_knowledge_base_count: int
    candidate_count: int
    injected_count: int
    rejected_count: int
    citation_count: int
    reranker: str
    clarification_required: bool
    route_label: str
    authorized_context_count: int
    retrieved_chunk_count: int
    intent: str
    structured_entity_types: tuple[str, ...]
    budget_truncated: bool
    reply_length: int
    plan: RagAgentWorkflowPlan
    verification: RagAgentVerification


_PLANNER = DeterministicRagAgentPlanner()
_VERIFIER = DeterministicRagAgentVerifier()


def plan_workflow(state: RagAgentGraphState) -> RagAgentGraphState:
    """Build a redacted RAG Agent workflow plan from service-layer metadata."""
    return {
        "plan": _PLANNER.plan(
            retrieval_route=state["retrieval_route"],
            answer_mode=state["answer_mode"],
            document_scope=state["document_scope"],
            resolved_knowledge_base_count=state["resolved_knowledge_base_count"],
            candidate_count=state.get("candidate_count", 0),
            injected_count=state.get("injected_count", 0),
            rejected_count=state.get("rejected_count", 0),
            citation_count=state.get("citation_count", 0),
            reranker=state.get("reranker", "deterministic"),
            clarification_required=state.get("clarification_required", False),
            route_label=state.get("route_label", "general_assistant"),
            authorized_context_count=state.get("authorized_context_count", 0),
            retrieved_chunk_count=state.get("retrieved_chunk_count", 0),
            intent=state.get("intent", "semantic_qa"),
            structured_entity_types=tuple(state.get("structured_entity_types", ())),
            budget_truncated=state.get("budget_truncated", False),
            reply_length=state.get("reply_length", 0),
        )
    }


def verify_workflow(state: RagAgentGraphState) -> RagAgentGraphState:
    """Verify the planned trace contract before clients can consume it."""
    return {"verification": _VERIFIER.verify(state["plan"])}


def build_graph():
    """Build and compile the dedicated RAG Agent LangGraph form."""
    graph = StateGraph(RagAgentGraphState)
    graph.add_node("plan_workflow", plan_workflow)
    graph.add_node("verify_workflow", verify_workflow)
    graph.add_edge(START, "plan_workflow")
    graph.add_edge("plan_workflow", "verify_workflow")
    graph.add_edge("verify_workflow", END)
    return graph.compile(checkpointer=False)


@lru_cache(maxsize=1)
def get_rag_agent_graph():
    """Return the cached compiled RAG Agent contract graph."""
    return build_graph()


def invoke_rag_agent_graph(**state: object) -> RagAgentWorkflowPlan:
    """Run the RAG Agent graph and return a verified workflow plan."""
    result = cast(RagAgentGraphState, get_rag_agent_graph().invoke(state))
    plan = result.get("plan")
    verification = result.get("verification")
    if plan is None or verification is None:
        raise RuntimeError("RAG Agent graph did not produce a plan and verification")
    if not verification.passed:
        errors = "; ".join(verification.errors)
        raise RuntimeError(f"RAG Agent trace contract failed verification: {errors}")
    return plan
