"""ContextForge contract tests."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from my_agents.agents.context_forge.contracts import (
    ContextForgeRequest,
    ContextForgeResult,
    RetrievalEvidence,
    RetrievalPlan,
)
from my_agents.agents.context_forge.graph import invoke_context_forge_graph
from my_agents.agents.context_forge.planner import QueryCartographer
from my_agents.agents.context_forge.source_policy import SourceWarden
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.routing import RetrievalRoute, RetrievalRoutingDecision


def test_query_cartographer_detects_endpoint_enumeration_intent() -> None:
    plan = QueryCartographer().plan(
        message="List the API endpoints in this document",
        history=[HumanMessage(content="I uploaded an API reference")],
        authorized_document_count=1,
    )

    assert plan.intent == "enumeration"
    assert plan.structured_entity_types == ("api_endpoint",)
    assert plan.route_decision.route == "retrieval_required"
    assert plan.limits.injected_limit >= 5


def test_query_cartographer_requires_structured_retrieval_when_docs_exist() -> None:
    plan = QueryCartographer().plan(
        message="Show env vars",
        history=[],
        authorized_document_count=1,
    )

    assert plan.intent == "enumeration"
    assert plan.structured_entity_types == ("config_key",)
    assert plan.route_decision.route == "retrieval_required"


def test_source_warden_preserves_resolved_source_boundary() -> None:
    selection_context = KnowledgeBaseSelectionContext(
        mode="selected",
        knowledge_base_ids=("group-kb", "personal-kb"),
        resolved_knowledge_base_ids=("group-kb", "personal-kb"),
        resolved_count=2,
    )

    request = ContextForgeRequest(
        user_id="user-1",
        conversation_id="conversation-1",
        query="Show documented commands",
        messages=[],
        selection_context=selection_context,
    )

    assert SourceWarden().knowledge_base_ids(request.selection_context) == (
        "group-kb",
        "personal-kb",
    )


def test_context_forge_graph_retries_required_retrieval_with_evidence_terms() -> None:
    service = RecordingContextForgeService(route="retrieval_required")
    request = ContextForgeRequest(
        user_id="user-1",
        conversation_id="conversation-1",
        query="Summarize my uploaded document",
        messages=[],
        selection_context=KnowledgeBaseSelectionContext(
            mode="all",
            knowledge_base_ids=(),
            resolved_count=0,
        ),
    )

    result = invoke_context_forge_graph(request=request, service=service)

    assert service.queries == [
        "Summarize my uploaded document",
        "Summarize my uploaded document authorized document source citation evidence",
    ]
    assert result.retrieval_attempt_count == 2
    assert result.insufficient_evidence is True


def test_context_forge_graph_skips_retry_for_no_retrieval_route() -> None:
    service = RecordingContextForgeService(route="no_retrieval")
    request = ContextForgeRequest(
        user_id="user-1",
        conversation_id="conversation-1",
        query="What is RAG?",
        messages=[],
        selection_context=KnowledgeBaseSelectionContext(
            mode="all",
            knowledge_base_ids=(),
            resolved_count=0,
        ),
    )

    result = invoke_context_forge_graph(request=request, service=service)

    assert service.queries == ["What is RAG?"]
    assert result.retrieval_attempt_count == 1
    assert result.insufficient_evidence is False


class RecordingContextForgeService:
    def __init__(self, *, route: RetrievalRoute) -> None:
        self._route = route
        self.queries: list[str] = []

    def retrieve(self, request: ContextForgeRequest) -> ContextForgeResult:
        self.queries.append(request.query)
        decision = RetrievalRoutingDecision(
            route=self._route,
            reason="test route",
            rewritten_query=request.query,
            document_scope="user_documents",
        )
        plan = RetrievalPlan(
            intent="semantic_qa",
            original_query=request.query,
            rewritten_query=request.query,
            route_decision=decision,
        )
        return ContextForgeResult(
            plan=plan,
            decision=decision,
            answer_mode="general_knowledge",
            retrieved_chunks=[],
            retrieval_latency_ms=1.0,
            evidence=RetrievalEvidence(
                intent="semantic_qa",
                candidate_count=0,
                injected_count=0,
                rejected_count=0,
                source_counts={},
                structured_entity_types=(),
            ),
        )
