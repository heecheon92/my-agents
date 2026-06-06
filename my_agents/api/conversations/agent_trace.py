"""Compact localized agent-trace payloads for conversation runs.

The trace is intentionally product-facing and redacted: it exposes phase names,
statuses, counts, and stable localization copy, not hidden chain-of-thought or raw
retrieval snippets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.conversations.schemas import AgentTraceStep, AgentTraceText
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision
from my_agents.schemas import RouteDecision


def retrieval_agent_trace_steps(
    *,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    retrieval_evidence: RetrievalEvidence | None = None,
) -> list[AgentTraceStep]:
    """Return compact ContextForge trace steps for retrieval completion."""
    candidate_count = retrieval_evidence.candidate_count if retrieval_evidence is not None else 0
    injected_count = (
        retrieval_evidence.injected_count if retrieval_evidence is not None else len(retrieved_chunks)
    )
    rejected_count = retrieval_evidence.rejected_count if retrieval_evidence is not None else 0
    reranker = retrieval_evidence.reranker if retrieval_evidence is not None else "deterministic"
    structured_types = (
        list(retrieval_evidence.structured_entity_types) if retrieval_evidence is not None else []
    )
    intent = retrieval_evidence.intent if retrieval_evidence is not None else "semantic_qa"
    retrieval_active = answer_mode != "general_knowledge" or bool(retrieved_chunks)
    retrieval_status = "completed" if retrieval_active else "skipped"
    rerank_status = "completed" if candidate_count else "skipped"
    curator_status = "completed" if injected_count else "skipped"

    return [
        AgentTraceStep(
            id="query_cartographer",
            event_type="retrieval_completed",
            status="completed",
            title=AgentTraceText(en="Query Cartographer", ko="질문 지도화"),
            description=AgentTraceText(
                en=f"Planned a {retrieval_decision.route} path for {intent}.",
                ko=f"{intent} 요청을 {retrieval_decision.route} 경로로 계획했습니다.",
            ),
            evidence={
                "retrieval_route": retrieval_decision.route,
                "answer_mode": answer_mode,
                "document_scope": retrieval_decision.document_scope,
                "intent": intent,
                "structured_entity_types": structured_types,
            },
        ),
        AgentTraceStep(
            id="source_warden",
            event_type="retrieval_completed",
            status="completed",
            title=AgentTraceText(en="Source Warden", ko="출처 경계 확인"),
            description=AgentTraceText(
                en=f"Resolved {selection_context.resolved_count} authorized knowledge sources.",
                ko=f"승인된 지식 출처 {selection_context.resolved_count}개를 확정했습니다.",
            ),
            evidence={
                "resolved_knowledge_base_count": selection_context.resolved_count,
                "mandatory_group_knowledge_base_count": len(
                    selection_context.mandatory_group_knowledge_base_ids
                ),
                "optional_personal_knowledge_base_count": len(
                    selection_context.optional_personal_knowledge_base_ids
                ),
            },
        ),
        AgentTraceStep(
            id="candidate_scouts",
            event_type="retrieval_completed",
            status=retrieval_status,
            title=AgentTraceText(en="Candidate Scouts", ko="후보 검색"),
            description=AgentTraceText(
                en=f"Found {candidate_count} redacted candidates and {len(retrieved_chunks)} context chunks.",
                ko=f"후보 {candidate_count}개와 컨텍스트 청크 {len(retrieved_chunks)}개를 확인했습니다.",
            ),
            evidence={
                "candidate_count": candidate_count,
                "authorized_context_count": len(retrieved_chunks),
            },
        ),
        AgentTraceStep(
            id="evidence_judge",
            event_type="retrieval_completed",
            status=rerank_status,
            title=AgentTraceText(en="Evidence Judge", ko="근거 판정"),
            description=AgentTraceText(
                en=f"Applied {reranker} relevance ordering without exposing snippets.",
                ko=f"스니펫을 노출하지 않고 {reranker} 관련도 정렬을 적용했습니다.",
            ),
            evidence={"reranker": reranker, "candidate_count": candidate_count},
        ),
        AgentTraceStep(
            id="context_curator",
            event_type="retrieval_completed",
            status=curator_status,
            title=AgentTraceText(en="Context Curator", ko="컨텍스트 선별"),
            description=AgentTraceText(
                en=f"Injected {injected_count} chunks and rejected {rejected_count} over budget or limits.",
                ko=f"청크 {injected_count}개를 주입하고 {rejected_count}개를 예산/한도로 제외했습니다.",
            ),
            evidence={
                "injected_count": injected_count,
                "rejected_count": rejected_count,
                "budget_truncated": (
                    retrieval_evidence.budget_truncated if retrieval_evidence is not None else False
                ),
            },
        ),
    ]


def graph_agent_trace_step(
    *,
    route: RouteDecision,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
) -> AgentTraceStep:
    """Return the assistant graph invocation trace step."""
    return AgentTraceStep(
        id="assistant_graph",
        event_type="graph_invoked",
        status="completed",
        title=AgentTraceText(en="Assistant Graph", ko="어시스턴트 그래프"),
        description=AgentTraceText(
            en=f"Invoked {route.label} with {len(retrieved_chunks)} redacted context chunks.",
            ko=f"{route.label} 경로에 컨텍스트 청크 {len(retrieved_chunks)}개를 전달했습니다.",
        ),
        evidence={
            "route_label": route.label,
            "retrieval_route": retrieval_decision.route,
            "answer_mode": answer_mode,
            "retrieved_chunk_count": len(retrieved_chunks),
        },
    )


def answer_agent_trace_step(
    *,
    citation_count: int,
    reply: str,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    clarification_required: bool = False,
) -> AgentTraceStep:
    """Return the answer-composition trace step."""
    status = "waiting" if clarification_required else "completed"
    return AgentTraceStep(
        id="answer_composer",
        event_type="answer_composed",
        status=status,
        title=AgentTraceText(en="Answer Composer", ko="답변 작성"),
        description=AgentTraceText(
            en=f"Prepared an answer with {citation_count} citations.",
            ko=f"인용 {citation_count}개와 함께 답변을 준비했습니다.",
        ),
        evidence={
            "citation_count": citation_count,
            "reply_length": len(reply),
            "retrieval_route": retrieval_decision.route,
            "answer_mode": answer_mode,
            "clarification_required": clarification_required,
        },
    )


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
    steps = retrieval_agent_trace_steps(
        retrieved_chunks=retrieved_chunks,
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        selection_context=selection_context,
        retrieval_evidence=retrieval_evidence,
    )
    if not clarification_required:
        steps.append(
            graph_agent_trace_step(
                route=route,
                retrieved_chunks=retrieved_chunks,
                retrieval_decision=retrieval_decision,
                answer_mode=answer_mode,
            )
        )
    steps.append(
        answer_agent_trace_step(
            citation_count=citation_count,
            reply=reply,
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            clarification_required=clarification_required,
        )
    )
    return steps


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
