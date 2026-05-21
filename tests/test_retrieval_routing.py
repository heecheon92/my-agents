"""Deterministic retrieval-routing policy tests."""

from __future__ import annotations

from my_agents.knowledge.routing import answer_mode_for_route, route_retrieval


def test_korean_contract_prompt_requires_retrieval() -> None:
    decision = route_retrieval(message="계약서에서 해지 조항 알려줘")

    assert decision.route == "retrieval_required"
    assert decision.document_scope == "unknown"


def test_uploaded_material_summary_requires_retrieval() -> None:
    decision = route_retrieval(message="내가 올린 자료 요약해줘")

    assert decision.route == "retrieval_required"
    assert decision.document_scope == "user_documents"


def test_general_rag_concept_skips_retrieval() -> None:
    decision = route_retrieval(message="RAG가 뭐야?")

    assert decision.route == "no_retrieval"
    assert (
        answer_mode_for_route(decision=decision, relevant_context_found=False)
        == "general_knowledge"
    )


def test_service_architecture_prompt_uses_optional_retrieval() -> None:
    decision = route_retrieval(message="우리 서비스 인증 로직 어떻게 정리하면 좋을까?")

    assert decision.route == "retrieval_optional"
    assert decision.document_scope == "unknown"


def test_ambiguous_document_reference_requires_clarification_with_multiple_docs() -> None:
    decision = route_retrieval(
        message="이 문서 기준으로 개선점을 알려줘",
        authorized_document_count=2,
    )

    assert decision.route == "clarification_required"
    assert decision.document_scope == "unknown"
