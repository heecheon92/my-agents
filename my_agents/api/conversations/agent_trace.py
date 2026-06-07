"""Compact localized agent-trace payloads for conversation runs.

The trace is intentionally product-facing and redacted: it exposes phase names,
statuses, counts, and stable localization copy, not hidden chain-of-thought or raw
retrieval snippets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from my_agents.agents.agentic_rag.contracts import AgenticRagStage
from my_agents.agents.agentic_rag.planner import DeterministicAgenticRagPlanner
from my_agents.agents.agentic_rag.verifier import DeterministicAgenticRagVerifier
from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.conversations.schemas import AgentTraceStep, AgentTraceText
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision
from my_agents.schemas import RouteDecision

_RETRIEVAL_STAGE_IDS = {
    "query_cartographer",
    "source_warden",
    "candidate_scouts",
    "evidence_judge",
    "context_curator",
}
_PLANNER = DeterministicAgenticRagPlanner()
_VERIFIER = DeterministicAgenticRagVerifier()


def retrieval_agent_trace_steps(
    *,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    retrieval_evidence: RetrievalEvidence | None = None,
) -> list[AgentTraceStep]:
    """Return compact ContextForge trace steps for retrieval completion."""
    return [
        _stage_to_trace_step(stage, event_type="retrieval_completed")
        for stage in _verified_agentic_rag_stages(
            retrieved_chunks=retrieved_chunks,
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            selection_context=selection_context,
            retrieval_evidence=retrieval_evidence,
        )
        if stage.id in _RETRIEVAL_STAGE_IDS
    ]


def graph_agent_trace_step(
    *,
    route: RouteDecision,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    retrieval_evidence: RetrievalEvidence | None = None,
) -> AgentTraceStep:
    """Return the assistant graph invocation trace step."""
    stage = _stage_by_id(
        "assistant_graph",
        retrieved_chunks=retrieved_chunks,
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        selection_context=selection_context,
        route=route,
        retrieval_evidence=retrieval_evidence,
    )
    return _stage_to_trace_step(stage, event_type="graph_invoked")


def answer_agent_trace_step(
    *,
    citation_count: int,
    reply: str,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    route: RouteDecision | None = None,
    retrieved_chunks: list[RetrievedChunk] | None = None,
    retrieval_evidence: RetrievalEvidence | None = None,
    clarification_required: bool = False,
) -> AgentTraceStep:
    """Return the answer-composition trace step."""
    stage = _stage_by_id(
        "answer_composer",
        retrieved_chunks=retrieved_chunks or [],
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        selection_context=selection_context,
        route=route,
        citation_count=citation_count,
        reply=reply,
        retrieval_evidence=retrieval_evidence,
        clarification_required=clarification_required,
    )
    return _stage_to_trace_step(stage, event_type="answer_composed")


def conversation_agent_trace_steps(
    *,
    route: RouteDecision,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    citation_count: int,
    reply: str,
    retrieval_evidence: RetrievalEvidence | None = None,
    clarification_required: bool = False,
) -> list[AgentTraceStep]:
    """Return the compact end-to-end trace for a completed run response."""
    stages = _verified_agentic_rag_stages(
        retrieved_chunks=retrieved_chunks,
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        selection_context=selection_context,
        route=route,
        citation_count=citation_count,
        reply=reply,
        retrieval_evidence=retrieval_evidence,
        clarification_required=clarification_required,
    )
    return [
        _stage_to_trace_step(
            stage,
            event_type=_event_type_for_stage(stage),
        )
        for stage in stages
        if not (clarification_required and stage.id == "assistant_graph")
    ]


def agent_trace_payload(steps: Iterable[AgentTraceStep]) -> list[dict[str, Any]]:
    """Serialize trace steps for raw event/SSE payload dictionaries."""
    return [step.model_dump(mode="json") for step in steps]


def agent_trace_steps_from_event_payloads(
    payloads: Iterable[Mapping[str, Any]],
) -> list[AgentTraceStep]:
    """Reconstruct persisted trace steps from event payloads for run-detail refresh."""
    steps: list[AgentTraceStep] = []
    seen: set[tuple[str, str]] = set()
    for payload in payloads:
        raw_steps = payload.get("agent_trace", [])
        if not isinstance(raw_steps, list):
            continue
        for raw_step in raw_steps:
            if not isinstance(raw_step, Mapping):
                continue
            step = AgentTraceStep.model_validate(dict(raw_step))
            key = (step.id, step.event_type)
            if key in seen:
                continue
            seen.add(key)
            steps.append(step)
    return steps


def _verified_agentic_rag_stages(
    *,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    route: RouteDecision | None = None,
    citation_count: int = 0,
    reply: str = "",
    retrieval_evidence: RetrievalEvidence | None = None,
    clarification_required: bool = False,
) -> tuple[AgenticRagStage, ...]:
    candidate_count = (
        retrieval_evidence.candidate_count
        if retrieval_evidence is not None
        else len(retrieved_chunks)
    )
    injected_count = (
        retrieval_evidence.injected_count
        if retrieval_evidence is not None
        else len(retrieved_chunks)
    )
    plan = _PLANNER.plan(
        retrieval_route=retrieval_decision.route,
        answer_mode=answer_mode,
        document_scope=retrieval_decision.document_scope,
        resolved_knowledge_base_count=selection_context.resolved_count,
        candidate_count=candidate_count,
        injected_count=injected_count,
        rejected_count=retrieval_evidence.rejected_count if retrieval_evidence is not None else 0,
        citation_count=citation_count,
        reranker=retrieval_evidence.reranker if retrieval_evidence is not None else "deterministic",
        clarification_required=clarification_required,
        route_label=route.label if route is not None else "general_assistant",
        mandatory_group_knowledge_base_count=len(
            selection_context.mandatory_group_knowledge_base_ids
        ),
        optional_personal_knowledge_base_count=len(
            selection_context.optional_personal_knowledge_base_ids
        ),
        authorized_context_count=len(retrieved_chunks),
        retrieved_chunk_count=len(retrieved_chunks),
        intent=retrieval_evidence.intent if retrieval_evidence is not None else "semantic_qa",
        structured_entity_types=(
            tuple(retrieval_evidence.structured_entity_types)
            if retrieval_evidence is not None
            else ()
        ),
        budget_truncated=(
            retrieval_evidence.budget_truncated if retrieval_evidence is not None else False
        ),
        reply_length=len(reply),
    )
    verification = _VERIFIER.verify(plan)
    if not verification.passed:
        errors = "; ".join(verification.errors)
        raise RuntimeError(f"Agentic RAG trace contract failed verification: {errors}")
    return plan.stages


def _stage_by_id(stage_id: str, **kwargs: Any) -> AgenticRagStage:
    for stage in _verified_agentic_rag_stages(**kwargs):
        if stage.id == stage_id:
            return stage
    raise RuntimeError(f"Agentic RAG trace stage {stage_id!r} is unavailable")


def _stage_to_trace_step(stage: AgenticRagStage, *, event_type: str) -> AgentTraceStep:
    return AgentTraceStep(
        id=stage.id,
        event_type=event_type,
        status=stage.status,
        title=AgentTraceText(en=stage.title.en, ko=stage.title.ko),
        description=AgentTraceText(en=stage.description.en, ko=stage.description.ko),
        evidence=stage.evidence,
    )


def _event_type_for_stage(stage: AgenticRagStage) -> str:
    if stage.id in _RETRIEVAL_STAGE_IDS:
        return "retrieval_completed"
    if stage.id == "assistant_graph":
        return "graph_invoked"
    return "answer_composed"
