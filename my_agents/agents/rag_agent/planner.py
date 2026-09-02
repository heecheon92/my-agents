"""Deterministic planner for RAG Agent workflow trace contracts."""

from __future__ import annotations

from my_agents.agents.rag_agent.contracts import (
    ASSISTANT_AGENT_NAME,
    RETRIEVAL_AGENT_NAME,
    LocalizedRagAgentText,
    RagAgentOperationalSummary,
    RagAgentStage,
    RagAgentWorkflowPlan,
)
from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute


class DeterministicRagAgentPlanner:
    """Plan compact workflow stages without provider calls or hidden reasoning."""

    def plan(
        self,
        *,
        retrieval_route: RetrievalRoute,
        answer_mode: AnswerMode,
        document_scope: DocumentScope,
        resolved_knowledge_base_count: int,
        candidate_count: int = 0,
        injected_count: int = 0,
        rejected_count: int = 0,
        citation_count: int = 0,
        reranker: str = "deterministic",
        clarification_required: bool = False,
        route_label: str = "general_assistant",
        authorized_context_count: int = 0,
        retrieved_chunk_count: int = 0,
        intent: str = "semantic_qa",
        structured_entity_types: tuple[str, ...] = (),
        budget_truncated: bool = False,
        reply_length: int = 0,
    ) -> RagAgentWorkflowPlan:
        """Build the frontend-safe stage plan for one run.

        ContextForge remains the internal delegated retrieval implementation. The
        planner only summarizes stage state from already-redacted service-layer counts.
        """
        retrieval_active = answer_mode != "general_knowledge" or candidate_count > 0
        scout_status = "completed" if retrieval_active else "skipped"
        judge_status = "completed" if candidate_count else "skipped"
        curator_status = "completed" if injected_count else "skipped"
        assistant_status = "waiting" if clarification_required else "completed"
        graph_status = "skipped" if clarification_required else "completed"
        context_curator_evidence: dict[str, object] = {
            "injected_count": injected_count,
            "rejected_count": rejected_count,
        }
        if budget_truncated:
            context_curator_evidence["budget_truncated"] = budget_truncated

        stages = (
            RagAgentStage(
                id="query_cartographer",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status="completed",
                title=LocalizedRagAgentText(en="Query Cartographer", ko="질문 지도화"),
                description=LocalizedRagAgentText(
                    en=f"Planned a {retrieval_route} path for {intent}.",
                    ko=f"{intent} 요청을 {retrieval_route} 경로로 계획했습니다.",
                ),
                evidence={
                    "retrieval_route": retrieval_route,
                    "answer_mode": answer_mode,
                    "document_scope": document_scope,
                    "intent": intent,
                    "structured_entity_types": list(structured_entity_types),
                },
                operational_summary=RagAgentOperationalSummary(
                    schema_version=1,
                    message_key="agent_trace.query_planned",
                    parameters={
                        "retrieval_route": retrieval_route,
                        "document_scope": document_scope,
                    },
                ),
            ),
            RagAgentStage(
                id="source_warden",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status="completed",
                title=LocalizedRagAgentText(en="Source Warden", ko="출처 경계 확인"),
                description=LocalizedRagAgentText(
                    en=f"Resolved {resolved_knowledge_base_count} authorized sources.",
                    ko=f"승인된 출처 {resolved_knowledge_base_count}개를 확정했습니다.",
                ),
                evidence={
                    "resolved_knowledge_base_count": resolved_knowledge_base_count,
                },
                operational_summary=RagAgentOperationalSummary(
                    schema_version=1,
                    message_key="agent_trace.sources_resolved",
                    parameters={
                        "resolved_knowledge_base_count": resolved_knowledge_base_count,
                    },
                ),
            ),
            RagAgentStage(
                id="candidate_scouts",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status=scout_status,
                title=LocalizedRagAgentText(en="Candidate Scouts", ko="후보 검색"),
                description=LocalizedRagAgentText(
                    en=f"Found {candidate_count} candidates; {authorized_context_count} chunks.",
                    ko=f"후보 {candidate_count}개, 청크 {authorized_context_count}개 확인.",
                ),
                evidence={
                    "candidate_count": candidate_count,
                    "authorized_context_count": authorized_context_count,
                },
                operational_summary=(
                    RagAgentOperationalSummary(
                        schema_version=1,
                        message_key="agent_trace.candidates_found",
                        parameters={
                            "candidate_count": candidate_count,
                            "authorized_context_count": authorized_context_count,
                        },
                    )
                    if scout_status == "completed"
                    else None
                ),
            ),
            RagAgentStage(
                id="evidence_judge",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status=judge_status,
                title=LocalizedRagAgentText(en="Evidence Judge", ko="근거 판정"),
                description=LocalizedRagAgentText(
                    en="Applied relevance ordering.",
                    ko="관련도 정렬을 적용했습니다.",
                ),
                evidence={"reranker": reranker, "candidate_count": candidate_count},
                operational_summary=(
                    RagAgentOperationalSummary(
                        schema_version=1,
                        message_key="agent_trace.relevance_ordered",
                        parameters={"candidate_count": candidate_count},
                    )
                    if judge_status == "completed"
                    else None
                ),
            ),
            RagAgentStage(
                id="context_curator",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status=curator_status,
                title=LocalizedRagAgentText(en="Context Curator", ko="컨텍스트 선별"),
                description=LocalizedRagAgentText(
                    en=f"Injected {injected_count} chunks and rejected {rejected_count}.",
                    ko=f"청크 {injected_count}개를 주입하고 {rejected_count}개를 제외했습니다.",
                ),
                evidence=context_curator_evidence,
                operational_summary=(
                    RagAgentOperationalSummary(
                        schema_version=1,
                        message_key="agent_trace.context_prepared",
                        parameters={
                            "injected_count": injected_count,
                            "rejected_count": rejected_count,
                            "budget_truncated": budget_truncated,
                        },
                    )
                    if curator_status == "completed"
                    else None
                ),
            ),
            RagAgentStage(
                id="assistant_graph",
                role="assistant_agent",
                agent_name=ASSISTANT_AGENT_NAME,
                status=graph_status,
                title=LocalizedRagAgentText(en="Assistant Graph", ko="어시스턴트 그래프"),
                description=LocalizedRagAgentText(
                    en=f"Invoked {route_label} with {retrieved_chunk_count} context chunks.",
                    ko=f"{route_label} 경로에 컨텍스트 청크 {retrieved_chunk_count}개 전달.",
                ),
                evidence={
                    "route_label": route_label,
                    "retrieval_route": retrieval_route,
                    "answer_mode": answer_mode,
                    "retrieved_chunk_count": retrieved_chunk_count,
                },
                operational_summary=(
                    RagAgentOperationalSummary(
                        schema_version=1,
                        message_key="agent_trace.graph_invoked",
                        parameters={"retrieved_chunk_count": retrieved_chunk_count},
                    )
                    if graph_status == "completed"
                    else None
                ),
            ),
            RagAgentStage(
                id="answer_composer",
                role="assistant_agent",
                agent_name=ASSISTANT_AGENT_NAME,
                status=assistant_status,
                title=LocalizedRagAgentText(en="Answer Composer", ko="답변 작성"),
                description=LocalizedRagAgentText(
                    en=f"Prepared answer state with {citation_count} citations.",
                    ko=f"인용 {citation_count}개의 답변 상태를 준비했습니다.",
                ),
                evidence={
                    "citation_count": citation_count,
                    "reply_length": reply_length,
                    "retrieval_route": retrieval_route,
                    "answer_mode": answer_mode,
                    "clarification_required": clarification_required,
                },
                operational_summary=RagAgentOperationalSummary(
                    schema_version=1,
                    message_key=(
                        "agent_trace.clarification_requested"
                        if clarification_required
                        else "agent_trace.answer_prepared"
                    ),
                    parameters=(
                        {} if clarification_required else {"citation_count": citation_count}
                    ),
                ),
            ),
        )
        return RagAgentWorkflowPlan(
            retrieval_route=retrieval_route,
            answer_mode=answer_mode,
            document_scope=document_scope,
            stages=stages,
        )
