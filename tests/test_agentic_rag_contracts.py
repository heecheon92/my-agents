"""Agentic RAG workflow contract tests."""

from __future__ import annotations

from dataclasses import replace

from my_agents.agents.agentic_rag import (
    EXPECTED_STAGE_ORDER,
    RETRIEVAL_AGENT_NAME,
    DeterministicAgenticRagPlanner,
    DeterministicAgenticRagVerifier,
)


def test_agentic_rag_planner_uses_contextforge_as_retrieval_agent() -> None:
    plan = DeterministicAgenticRagPlanner().plan(
        retrieval_route="retrieval_required",
        answer_mode="grounded",
        document_scope="selected",
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
    assert plan.stages[2].id == "candidate_scouts"
    assert plan.stages[2].status == "completed"
    assert plan.stages[4].evidence == {"injected_count": 3, "rejected_count": 5}
    assert plan.stages[0].title.ko == "질문 지도화"
    assert DeterministicAgenticRagVerifier().verify(plan).passed is True


def test_agentic_rag_planner_marks_retrieval_stages_skipped_for_general_answer() -> None:
    plan = DeterministicAgenticRagPlanner().plan(
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
    assert DeterministicAgenticRagVerifier().verify(plan).errors == ()


def test_agentic_rag_verifier_rejects_unsafe_trace_evidence() -> None:
    plan = DeterministicAgenticRagPlanner().plan(
        retrieval_route="retrieval_required",
        answer_mode="grounded",
        document_scope="all",
        resolved_knowledge_base_count=1,
        candidate_count=1,
        injected_count=1,
    )
    unsafe_stage = replace(plan.stages[0], evidence={"prompt": "raw user prompt must not leak"})
    unsafe_plan = replace(plan, stages=(unsafe_stage, *plan.stages[1:]))

    result = DeterministicAgenticRagVerifier().verify(unsafe_plan)

    assert result.passed is False
    assert "query_cartographer: unsafe evidence key 'prompt'" in result.errors
