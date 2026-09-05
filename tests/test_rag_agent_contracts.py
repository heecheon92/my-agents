"""RAG Agent workflow contract tests."""

from __future__ import annotations

from dataclasses import replace

from my_agents.agents.rag_agent import (
    EXPECTED_STAGE_ORDER,
    INTERNAL_RETRIEVAL_IMPLEMENTATION_NAME,
    RETRIEVAL_AGENT_NAME,
    DeterministicRagAgentGroundingVerifier,
    DeterministicRagAgentPlanner,
    DeterministicRagAgentVerifier,
    invoke_rag_agent_graph,
)
from my_agents.agents.rag_agent.contracts import RagAgentOperationalSummary
from my_agents.api.conversations.agent_trace import _stage_to_trace_step
from my_agents.api.conversations.answer_finalization import _verified_grounding_or_fallback
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import RetrievalRoutingDecision


def test_rag_agent_planner_uses_rag_agent_as_public_retrieval_agent() -> None:
    plan = DeterministicRagAgentPlanner().plan(
        retrieval_route="retrieval_required",
        answer_mode="document_grounded",
        document_scope="user_documents",
        resolved_knowledge_base_count=1,
        candidate_count=8,
        injected_count=3,
        rejected_count=5,
        citation_count=2,
        reranker="deterministic",
    )

    assert tuple(stage.id for stage in plan.stages) == EXPECTED_STAGE_ORDER
    assert {stage.agent_name for stage in plan.stages if stage.role == "retrieval_agent"} == {
        RETRIEVAL_AGENT_NAME
    }
    assert RETRIEVAL_AGENT_NAME == "RAG Agent"
    assert INTERNAL_RETRIEVAL_IMPLEMENTATION_NAME == "ContextForge"
    assert plan.stages[2].id == "candidate_scouts"
    assert plan.stages[2].status == "completed"
    assert plan.stages[4].evidence == {"injected_count": 3, "rejected_count": 5}
    assert plan.stages[0].title.ko == "질문 지도화"
    evidence_judge = plan.stages[3]
    assert evidence_judge.id == "evidence_judge"
    assert evidence_judge.description.en == "Applied relevance ordering."
    assert evidence_judge.description.ko == "관련도 정렬을 적용했습니다."
    assert evidence_judge.evidence["reranker"] == "deterministic"
    assert "deterministic" not in evidence_judge.description.en
    assert "deterministic" not in evidence_judge.description.ko
    assert evidence_judge.operational_summary == RagAgentOperationalSummary(
        schema_version=1,
        message_key="agent_trace.relevance_ordered",
        parameters={"candidate_count": 8},
    )
    assert plan.stages[0].operational_summary == RagAgentOperationalSummary(
        schema_version=1,
        message_key="agent_trace.query_planned",
        parameters={
            "retrieval_route": "retrieval_required",
            "document_scope": "user_documents",
        },
    )
    assert DeterministicRagAgentVerifier().verify(plan).passed is True


def test_rag_agent_planner_keeps_cross_encoder_mode_out_of_display_copy() -> None:
    plan = DeterministicRagAgentPlanner().plan(
        retrieval_route="retrieval_required",
        answer_mode="document_grounded",
        document_scope="user_documents",
        resolved_knowledge_base_count=1,
        candidate_count=3,
        reranker="cross_encoder",
    )

    evidence_judge = plan.stages[3]
    assert evidence_judge.evidence["reranker"] == "cross_encoder"
    assert evidence_judge.description.en == "Applied relevance ordering."
    assert evidence_judge.description.ko == "관련도 정렬을 적용했습니다."
    assert "cross_encoder" not in evidence_judge.description.en
    assert "cross_encoder" not in evidence_judge.description.ko


def test_rag_agent_graph_returns_verified_plan() -> None:
    plan = invoke_rag_agent_graph(
        retrieval_route="retrieval_required",
        answer_mode="document_grounded",
        document_scope="user_documents",
        resolved_knowledge_base_count=1,
        candidate_count=4,
        injected_count=2,
        citation_count=1,
        structured_entity_types=("api_endpoint",),
    )

    assert tuple(stage.id for stage in plan.stages) == EXPECTED_STAGE_ORDER
    assert plan.stages[0].evidence["structured_entity_types"] == ["api_endpoint"]
    assert DeterministicRagAgentVerifier().verify(plan).passed is True


def test_operational_summary_reaches_the_public_trace_step() -> None:
    plan = DeterministicRagAgentPlanner().plan(
        retrieval_route="retrieval_required",
        answer_mode="document_grounded",
        document_scope="user_documents",
        resolved_knowledge_base_count=2,
    )

    step = _stage_to_trace_step(plan.stages[1], event_type="retrieval_completed")

    assert step.model_dump(mode="json")["operational_summary"] == {
        "schema_version": 1,
        "message_key": "agent_trace.sources_resolved",
        "parameters": {"resolved_knowledge_base_count": 2},
    }


def test_rag_agent_planner_marks_retrieval_stages_skipped_for_general_answer() -> None:
    plan = DeterministicRagAgentPlanner().plan(
        retrieval_route="no_retrieval",
        answer_mode="general_knowledge",
        document_scope="unknown",
        resolved_knowledge_base_count=0,
        route_label="general_assistant",
    )

    statuses = {stage.id: stage.status for stage in plan.stages}
    assert statuses["candidate_scouts"] == "skipped"
    assert statuses["evidence_judge"] == "skipped"
    assert statuses["context_curator"] == "skipped"
    assert statuses["assistant_graph"] == "completed"
    assert statuses["answer_composer"] == "completed"
    assert plan.stages[2].operational_summary is None
    assert plan.stages[3].operational_summary is None
    assert plan.stages[4].operational_summary is None
    assert DeterministicRagAgentVerifier().verify(plan).errors == ()


def test_rag_agent_verifier_rejects_unsafe_trace_evidence() -> None:
    plan = DeterministicRagAgentPlanner().plan(
        retrieval_route="retrieval_required",
        answer_mode="document_grounded",
        document_scope="user_documents",
        resolved_knowledge_base_count=1,
        candidate_count=1,
        injected_count=1,
    )
    unsafe_stage = replace(plan.stages[0], evidence={"prompt": "raw user prompt must not leak"})
    unsafe_plan = replace(plan, stages=(unsafe_stage, *plan.stages[1:]))

    result = DeterministicRagAgentVerifier().verify(unsafe_plan)

    assert result.passed is False
    assert "query_cartographer: unsafe evidence key 'prompt'" in result.errors


def test_rag_agent_verifier_rejects_invalid_operational_summary_contract() -> None:
    plan = DeterministicRagAgentPlanner().plan(
        retrieval_route="retrieval_required",
        answer_mode="document_grounded",
        document_scope="user_documents",
        resolved_knowledge_base_count=1,
        candidate_count=2,
        injected_count=1,
    )
    invalid_stage = replace(
        plan.stages[3],
        operational_summary=RagAgentOperationalSummary(
            schema_version=1,
            message_key="agent_trace.relevance_ordered",
            parameters={"candidate_count": 2, "reranker": "cross_encoder"},
        ),
    )
    invalid_plan = replace(plan, stages=(*plan.stages[:3], invalid_stage, *plan.stages[4:]))

    result = DeterministicRagAgentVerifier().verify(invalid_plan)

    assert result.passed is False
    assert "evidence_judge: invalid operational summary parameters" in result.errors


def test_grounding_verifier_accepts_required_rag_with_relevant_consulted_source() -> None:
    result = DeterministicRagAgentGroundingVerifier().verify(
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="document_grounded",
        consulted_chunks=[
            RetrievedChunk(
                chunk=object(),
                document=object(),
                score=0.75,
                source="semantic_vector",
            )
        ],
        consulted_count=1,
        retrieval_attempt_count=1,
    )

    assert result.passed is True
    assert result.errors == ()


def test_grounding_verifier_rejects_required_rag_without_consulted_sources() -> None:
    result = DeterministicRagAgentGroundingVerifier().verify(
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="document_grounded",
        consulted_chunks=[],
        consulted_count=0,
        retrieval_attempt_count=2,
    )

    assert result.passed is False
    assert "required retrieval completions must consult source evidence" in result.errors


def test_grounding_verifier_accepts_required_retry_safe_fallback() -> None:
    result = DeterministicRagAgentGroundingVerifier().verify(
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="general_knowledge",
        consulted_chunks=[],
        consulted_count=0,
        insufficient_evidence=True,
        retrieval_attempt_count=2,
    )

    assert result.passed is True


def test_grounding_verifier_rejects_unretried_safe_fallback() -> None:
    result = DeterministicRagAgentGroundingVerifier().verify(
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="general_knowledge",
        consulted_chunks=[],
        consulted_count=0,
        insufficient_evidence=True,
        retrieval_attempt_count=1,
    )

    assert result.passed is False
    assert "required retrieval fallback must follow the bounded retry" in result.errors


def test_completion_grounding_gate_falls_back_for_required_rag_without_sources() -> None:
    reply, consulted_chunks, insufficient_evidence = _verified_grounding_or_fallback(
        reply="Unsupported answer",
        consulted_chunks=[],
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="document_grounded",
        retrieval_attempt_count=2,
    )

    assert consulted_chunks == []
    assert insufficient_evidence is True
    assert "enough relevant authorized document evidence" in reply
