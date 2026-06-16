"""Public RAG Agent retrieval boundary for document-grounded context.

The RAG Agent owns the assistant-facing retrieval tool/subgraph contract.  During
the current migration it delegates the low-level retrieval implementation to
ContextForge, which remains the permission-first retrieval engine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import BaseMessage
from sqlalchemy.orm import Session

from my_agents.agents.context_forge import invoke_context_forge_graph
from my_agents.agents.context_forge.contracts import ContextForgeRequest, RetrievalEvidence
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import (
    AnswerMode,
    RetrievalRoutingDecision,
    is_relevant_retrieval_result,
)
from my_agents.knowledge.source_locations import parse_source_location_json

_RETRIEVED_CONTEXT_SNIPPET_CHARS = 1200


@dataclass(frozen=True)
class RagAgentRetrievalResult:
    """RAG Agent retrieval output consumed by the general assistant graph."""

    decision: RetrievalRoutingDecision
    answer_mode: AnswerMode
    retrieved_chunks: list[RetrievedChunk]
    retrieval_latency_ms: float
    knowledge_base_selection: KnowledgeBaseSelectionContext
    retrieval_evidence: RetrievalEvidence | None = None
    retrieval_attempt_count: int = 1
    insufficient_evidence: bool = False


class RagAgentRuntime(Protocol):
    """Runtime-only RAG Agent retrieval dependency for graph nodes."""

    def retrieve_context(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message: str,
        messages: Sequence[BaseMessage],
        selection_context: KnowledgeBaseSelectionContext,
    ) -> RagAgentRetrievalResult:
        """Return authorized document context for one assistant turn."""
        ...


@dataclass(frozen=True)
class SqlAlchemyRagAgentRuntime:
    """DB-backed runtime adapter used by conversation-run service layers."""

    db: Session

    def retrieve_context(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message: str,
        messages: Sequence[BaseMessage],
        selection_context: KnowledgeBaseSelectionContext,
    ) -> RagAgentRetrievalResult:
        return retrieve_context(
            db=self.db,
            user_id=user_id,
            conversation_id=conversation_id,
            message=message,
            messages=messages,
            selection_context=selection_context,
        )


def retrieve_context(
    *,
    db: Session,
    user_id: str,
    conversation_id: str,
    message: str,
    messages: Sequence[BaseMessage],
    selection_context: KnowledgeBaseSelectionContext,
) -> RagAgentRetrievalResult:
    """Invoke the RAG Agent retrieval tool and return answer-ready context.

    ContextForge remains the delegated retrieval implementation behind this public
    boundary: it performs planning, authorization-aware candidate search, reranking,
    context packing, retry/sufficiency assessment, and redacted evidence capture.
    """
    graph_result = invoke_context_forge_graph(
        db=db,
        request=ContextForgeRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            query=message,
            messages=messages,
            selection_context=selection_context,
        ),
    )
    result = graph_result.result
    return RagAgentRetrievalResult(
        decision=result.decision,
        answer_mode=result.answer_mode,
        retrieved_chunks=result.retrieved_chunks,
        retrieval_latency_ms=result.retrieval_latency_ms,
        knowledge_base_selection=selection_context,
        retrieval_evidence=result.evidence,
        retrieval_attempt_count=graph_result.retrieval_attempt_count,
        insufficient_evidence=graph_result.insufficient_evidence,
    )


def chunks_used_for_answer(
    retrieval_result: RagAgentRetrievalResult,
) -> list[RetrievedChunk]:
    """Return retrieved chunks that are relevant enough to cite/inject."""
    if retrieval_result.answer_mode == "general_knowledge":
        return []
    return [
        item
        for item in retrieval_result.retrieved_chunks
        if is_relevant_retrieval_result(
            route=retrieval_result.decision.route,
            source=item.source,
            score=item.score,
        )
    ]


def retrieved_context_for_graph(retrieved_chunks: list[RetrievedChunk]) -> list[dict[str, object]]:
    """Return prompt-safe retrieved context dictionaries for assistant state."""
    return [
        {
            "document_id": item.document.id,
            "chunk_id": item.chunk.id,
            "knowledge_base_id": item.document.knowledge_base_id,
            "title": item.document.title,
            "snippet": item.chunk.content[:_RETRIEVED_CONTEXT_SNIPPET_CHARS],
            "source_page": item.chunk.source_page,
            "source_location_json": parse_source_location_json(item.chunk.source_location_json),
            "source_filename": item.document.source_filename,
            "source": item.source,
        }
        for item in retrieved_chunks
    ]
