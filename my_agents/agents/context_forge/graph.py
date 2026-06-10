"""LangGraph orchestration seam for ContextForge retrieval.

This graph intentionally wraps the existing permission-first ContextForge service
instead of moving authorization, SQL retrieval, ingestion, persistence, or provider
execution into graph prompts. It exists so current and future agents can treat
knowledge-base retrieval as a typed graph/tool capability.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy.orm import Session

from my_agents.agents.context_forge.contracts import (
    ContextForgeRequest,
    ContextForgeResult,
)
from my_agents.agents.context_forge.service import ContextForgeService
from my_agents.knowledge.routing import is_relevant_retrieval_result

_MAX_CONTEXTFORGE_RETRIES = 1


class ContextForgeRuntimeContext(TypedDict, total=False):
    """Runtime-only dependencies passed to LangGraph outside checkpointed state."""

    service: ContextForgeService


class ContextForgeGraphState(TypedDict, total=False):
    """State for the retrieval graph wrapper around ContextForge."""

    request: ContextForgeRequest
    result: ContextForgeResult
    retrieval_attempt_count: int
    insufficient_evidence: bool


@dataclass(frozen=True)
class ContextForgeGraphResult:
    """Stable result shape returned by the retrieval graph wrapper."""

    result: ContextForgeResult
    retrieval_attempt_count: int
    insufficient_evidence: bool


def retrieve_context(
    state: ContextForgeGraphState,
    runtime: Runtime[ContextForgeRuntimeContext],
) -> ContextForgeGraphState:
    """Run the first permission-first ContextForge retrieval attempt."""
    service = _context_forge_service(runtime)
    return {
        "result": service.retrieve(state["request"]),
        "retrieval_attempt_count": 1,
    }


def route_after_retrieve(state: ContextForgeGraphState) -> str:
    """Route to a bounded retry only when required evidence was not found."""
    result = state["result"]
    attempt_count = state.get("retrieval_attempt_count", 1)
    if attempt_count <= _MAX_CONTEXTFORGE_RETRIES and _requires_document_evidence_without_context(
        result
    ):
        return "retry_required_evidence"
    return "assess_evidence"


def retry_required_evidence(
    state: ContextForgeGraphState,
    runtime: Runtime[ContextForgeRuntimeContext],
) -> ContextForgeGraphState:
    """Retry required-document retrieval with explicit evidence terms."""
    service = _context_forge_service(runtime)
    request = state["request"]
    result = state["result"]
    retry_request = replace(
        request,
        query=_retry_query_for_required_evidence(
            request.query,
            result.decision.rewritten_query,
        ),
    )
    return {
        "request": retry_request,
        "result": service.retrieve(retry_request),
        "retrieval_attempt_count": state.get("retrieval_attempt_count", 1) + 1,
    }


def assess_evidence(state: ContextForgeGraphState) -> ContextForgeGraphState:
    """Record whether required retrieval still lacks enough authorized evidence."""
    return {
        "insufficient_evidence": _requires_document_evidence_without_context(state["result"]),
    }


def build_graph():
    """Build and compile the ContextForge retrieval graph."""
    graph = StateGraph(ContextForgeGraphState, context_schema=ContextForgeRuntimeContext)
    graph.add_node("retrieve_context", retrieve_context)
    graph.add_node("retry_required_evidence", retry_required_evidence)
    graph.add_node("assess_evidence", assess_evidence)
    graph.add_edge(START, "retrieve_context")
    graph.add_conditional_edges(
        "retrieve_context",
        route_after_retrieve,
        {
            "retry_required_evidence": "retry_required_evidence",
            "assess_evidence": "assess_evidence",
        },
    )
    graph.add_edge("retry_required_evidence", "assess_evidence")
    graph.add_edge("assess_evidence", END)
    return graph.compile()


@lru_cache(maxsize=1)
def get_context_forge_graph():
    """Return the cached compiled ContextForge retrieval graph."""
    return build_graph()


def invoke_context_forge_graph(
    *,
    request: ContextForgeRequest,
    db: Session | None = None,
    service: ContextForgeService | None = None,
) -> ContextForgeGraphResult:
    """Invoke the retrieval graph with a service or DB-backed service adapter."""
    runtime_service = service or _service_from_db(db)
    state = cast(
        ContextForgeGraphState,
        get_context_forge_graph().invoke(
            {"request": request},
            context={"service": runtime_service},
        ),
    )
    result = state.get("result")
    if result is None:
        raise RuntimeError("ContextForge retrieval graph did not produce a result")
    return ContextForgeGraphResult(
        result=result,
        retrieval_attempt_count=state.get("retrieval_attempt_count", 1),
        insufficient_evidence=state.get("insufficient_evidence", False),
    )


def _context_forge_service(runtime: Runtime[ContextForgeRuntimeContext]) -> ContextForgeService:
    service = (runtime.context or {}).get("service")
    if service is None:
        raise RuntimeError("ContextForge graph requires a runtime service")
    return service


def _service_from_db(db: Session | None) -> ContextForgeService:
    if db is None:
        raise ValueError("db is required when a ContextForgeService is not provided")
    return ContextForgeService(db)


def _requires_document_evidence_without_context(result: ContextForgeResult) -> bool:
    if result.decision.route != "retrieval_required":
        return False
    return not any(
        is_relevant_retrieval_result(
            route=result.decision.route,
            source=item.source,
            score=item.score,
        )
        for item in result.retrieved_chunks
    )


def _retry_query_for_required_evidence(message: str, rewritten_query: str) -> str:
    retry_terms = "authorized document source citation evidence"
    base_query = rewritten_query.strip() or message.strip()
    if retry_terms in base_query.casefold():
        return base_query
    return f"{base_query} {retry_terms}".strip()
