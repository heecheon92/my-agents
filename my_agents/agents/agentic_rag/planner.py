"""Deterministic planner for Agentic RAG workflow trace contracts."""

from __future__ import annotations

from my_agents.agents.agentic_rag.contracts import (
    ASSISTANT_AGENT_NAME,
    RETRIEVAL_AGENT_NAME,
    AgenticRagStage,
    AgenticRagWorkflowPlan,
    LocalizedAgenticRagText,
)
from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute


class DeterministicAgenticRagPlanner:
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
        mandatory_group_knowledge_base_count: int = 0,
        optional_personal_knowledge_base_count: int = 0,
        authorized_context_count: int = 0,
        retrieved_chunk_count: int = 0,
        intent: str = "semantic_qa",
        structured_entity_types: tuple[str, ...] = (),
        budget_truncated: bool = False,
        reply_length: int = 0,
    ) -> AgenticRagWorkflowPlan:
        """Build the frontend-safe stage plan for one run.

        ContextForge remains the retrieval agent. The planner only summarizes stage
        state from already-redacted service-layer counts.
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
            AgenticRagStage(
                id="query_cartographer",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status="completed",
                title=LocalizedAgenticRagText(en="Query Cartographer", ko="질문 지도화"),
                description=LocalizedAgenticRagText(
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
            ),
            AgenticRagStage(
                id="source_warden",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status="completed",
                title=LocalizedAgenticRagText(en="Source Warden", ko="출처 경계 확인"),
                description=LocalizedAgenticRagText(
                    en=f"Resolved {resolved_knowledge_base_count} authorized sources.",
                    ko=f"승인된 출처 {resolved_knowledge_base_count}개를 확정했습니다.",
                ),
                evidence={
                    "resolved_knowledge_base_count": resolved_knowledge_base_count,
                    "mandatory_group_knowledge_base_count": mandatory_group_knowledge_base_count,
                    "optional_personal_knowledge_base_count": (
                        optional_personal_knowledge_base_count
                    ),
                },
            ),
            AgenticRagStage(
                id="candidate_scouts",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status=scout_status,
                title=LocalizedAgenticRagText(en="Candidate Scouts", ko="후보 검색"),
                description=LocalizedAgenticRagText(
                    en=f"Found {candidate_count} candidates; {authorized_context_count} chunks.",
                    ko=f"후보 {candidate_count}개, 청크 {authorized_context_count}개 확인.",
                ),
                evidence={
                    "candidate_count": candidate_count,
                    "authorized_context_count": authorized_context_count,
                },
            ),
            AgenticRagStage(
                id="evidence_judge",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status=judge_status,
                title=LocalizedAgenticRagText(en="Evidence Judge", ko="근거 판정"),
                description=LocalizedAgenticRagText(
                    en=f"Applied {reranker} relevance ordering.",
                    ko=f"{reranker} 관련도 정렬을 적용했습니다.",
                ),
                evidence={"reranker": reranker, "candidate_count": candidate_count},
            ),
            AgenticRagStage(
                id="context_curator",
                role="retrieval_agent",
                agent_name=RETRIEVAL_AGENT_NAME,
                status=curator_status,
                title=LocalizedAgenticRagText(en="Context Curator", ko="컨텍스트 선별"),
                description=LocalizedAgenticRagText(
                    en=f"Injected {injected_count} chunks and rejected {rejected_count}.",
                    ko=f"청크 {injected_count}개를 주입하고 {rejected_count}개를 제외했습니다.",
                ),
                evidence=context_curator_evidence,
            ),
            AgenticRagStage(
                id="assistant_graph",
                role="assistant_agent",
                agent_name=ASSISTANT_AGENT_NAME,
                status=graph_status,
                title=LocalizedAgenticRagText(en="Assistant Graph", ko="어시스턴트 그래프"),
                description=LocalizedAgenticRagText(
                    en=f"Invoked {route_label} with {retrieved_chunk_count} context chunks.",
                    ko=f"{route_label} 경로에 컨텍스트 청크 {retrieved_chunk_count}개 전달.",
                ),
                evidence={
                    "route_label": route_label,
                    "retrieval_route": retrieval_route,
                    "answer_mode": answer_mode,
                    "retrieved_chunk_count": retrieved_chunk_count,
                },
            ),
            AgenticRagStage(
                id="answer_composer",
                role="assistant_agent",
                agent_name=ASSISTANT_AGENT_NAME,
                status=assistant_status,
                title=LocalizedAgenticRagText(en="Answer Composer", ko="답변 작성"),
                description=LocalizedAgenticRagText(
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
            ),
        )
        return AgenticRagWorkflowPlan(
            retrieval_route=retrieval_route,
            answer_mode=answer_mode,
            document_scope=document_scope,
            stages=stages,
        )
