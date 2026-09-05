"""Characterize completion policy without any database or provider runtime."""

import pytest

from my_agents.api.conversations.answer_finalization import prepare_answer
from my_agents.api.conversations.retrieval_context import ConversationRetrievalContext
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.routing import RetrievalRoutingDecision


def context(route="bypass", attempts=1):
    return ConversationRetrievalContext(
        decision=RetrievalRoutingDecision(
            route=route, reason="test", rewritten_query="test", document_scope="none"
        ),
        answer_mode="general_knowledge" if route == "bypass" else "document_grounded",
        retrieved_chunks=[],
        retrieval_latency_ms=0,
        knowledge_base_selection=KnowledgeBaseSelectionContext(
            mode="all", knowledge_base_ids=(), resolved_count=0
        ),
        retrieval_attempt_count=attempts,
    )


def test_general_answer_keeps_text_and_memory_snapshot():
    result = prepare_answer(
        base_reply="Hello",
        retrieval_context=context(),
        graph_state={},
        memory_source_snapshot='{"memory_count":1}',
    )
    assert result.reply == "Hello"
    assert result.consulted_chunks == []
    assert result.document_coverage is None
    assert result.memory_source_snapshot == '{"memory_count":1}'
    assert result.reasoning_summaries == []
    assert result.insufficient_evidence is False


def test_required_evidence_uses_existing_retry_and_grounding_policy():
    with pytest.raises(RuntimeError, match="grounding verification failed"):
        prepare_answer(
            base_reply="Unsupported",
            retrieval_context=context("retrieval_required"),
            graph_state={},
            memory_source_snapshot=None,
        )
    result = prepare_answer(
        base_reply="Unsupported",
        retrieval_context=context("retrieval_required", 2),
        graph_state={},
        memory_source_snapshot=None,
    )
    assert result.insufficient_evidence
    assert result.consulted_chunks == []
    assert "enough relevant authorized document evidence" in result.reply
