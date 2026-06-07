"""ContextForge contract tests."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from my_agents.agents.context_forge.contracts import ContextForgeRequest
from my_agents.agents.context_forge.planner import QueryCartographer
from my_agents.agents.context_forge.source_policy import SourceWarden
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext


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
