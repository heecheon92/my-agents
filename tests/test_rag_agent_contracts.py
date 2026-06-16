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
from my_agents.api.conversations.run_lifecycle import _verified_grounding_or_fallback
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
    assert DeterministicRagAgentVerifier().verify(plan).passed is True


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


def test_grounding_verifier_accepts_required_rag_with_relevant_citation() -> None:
    result = DeterministicRagAgentGroundingVerifier().verify(
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="document_grounded",
        cited_chunks=[
            RetrievedChunk(
                chunk=object(),
                document=object(),
                score=0.75,
                source="semantic_vector",
            )
        ],
        citation_count=1,
        retrieval_attempt_count=1,
    )

    assert result.passed is True
    assert result.errors == ()


def test_grounding_verifier_rejects_required_rag_without_citations() -> None:
    result = DeterministicRagAgentGroundingVerifier().verify(
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="document_grounded",
        cited_chunks=[],
        citation_count=0,
        retrieval_attempt_count=2,
    )

    assert result.passed is False
    assert "required retrieval completions must include citations" in result.errors


def test_grounding_verifier_accepts_required_retry_safe_fallback() -> None:
    result = DeterministicRagAgentGroundingVerifier().verify(
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="general_knowledge",
        cited_chunks=[],
        citation_count=0,
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
        cited_chunks=[],
        citation_count=0,
        insufficient_evidence=True,
        retrieval_attempt_count=1,
    )

    assert result.passed is False
    assert "required retrieval fallback must follow the bounded retry" in result.errors


def test_completion_grounding_gate_falls_back_for_required_rag_without_citations() -> None:
    reply, cited_chunks, insufficient_evidence = _verified_grounding_or_fallback(
        reply="Unsupported answer",
        cited_chunks=[],
        retrieval_decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="document requested",
            rewritten_query="document requested",
            document_scope="user_documents",
        ),
        answer_mode="document_grounded",
        retrieval_attempt_count=2,
    )

    assert cited_chunks == []
    assert insufficient_evidence is True
    assert "enough relevant authorized document evidence" in reply
