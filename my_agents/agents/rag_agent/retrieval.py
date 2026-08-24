"""Public RAG Agent retrieval boundary for document-grounded context.

The RAG Agent owns the assistant-facing retrieval tool/subgraph contract.  During
the current migration it delegates the low-level retrieval implementation to
ContextForge, which remains the permission-first retrieval engine.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import BaseMessage
from sqlalchemy.orm import Session

from my_agents.agents.context_forge import invoke_context_forge_graph
from my_agents.agents.context_forge.contracts import ContextForgeRequest, RetrievalEvidence
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import AuthorizedDocumentOption, RetrievalService, RetrievedChunk
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
        selected_document_id: str | None = None,
    ) -> RagAgentRetrievalResult:
        """Return authorized document context for one assistant turn."""
        ...

    def document_options(
        self,
        *,
        user_id: str,
        selection_context: KnowledgeBaseSelectionContext,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuthorizedDocumentOption], int]:
        """Return authorized document options for one interaction page."""
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
        selected_document_id: str | None = None,
    ) -> RagAgentRetrievalResult:
        return retrieve_context(
            db=self.db,
            user_id=user_id,
            conversation_id=conversation_id,
            message=message,
            messages=messages,
            selection_context=selection_context,
            selected_document_id=selected_document_id,
        )

    def document_options(
        self,
        *,
        user_id: str,
        selection_context: KnowledgeBaseSelectionContext,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuthorizedDocumentOption], int]:
        return RetrievalService(self.db).authorized_document_options(
            user_id=user_id,
            knowledge_base_ids=selection_context.retrieval_knowledge_base_ids,
            limit=limit,
            offset=offset,
        )


def retrieve_context(
    *,
    db: Session,
    user_id: str,
    conversation_id: str,
    message: str,
    messages: Sequence[BaseMessage],
    selection_context: KnowledgeBaseSelectionContext,
    selected_document_id: str | None = None,
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
            selected_document_id=selected_document_id,
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


def retrieved_context_for_graph(
    retrieved_chunks: list[RetrievedChunk],
    *,
    hidden_knowledge_base_ids: Collection[str] = (),
) -> list[dict[str, object]]:
    """Return prompt-safe context, omitting provenance for hidden system sources."""
    hidden_ids = set(hidden_knowledge_base_ids)
    context: list[dict[str, object]] = []
    for item in retrieved_chunks:
        if item.document.knowledge_base_id in hidden_ids:
            context.append({"snippet": item.chunk.content[:_RETRIEVED_CONTEXT_SNIPPET_CHARS]})
            continue
        context.append(
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
                "score": item.score,
            }
        )
    return context


def rag_result_snapshot_for_graph(result: RagAgentRetrievalResult) -> dict[str, object]:
    """Return checkpoint-safe metadata without ORM-backed retrieved chunks."""
    evidence = result.retrieval_evidence
    return {
        "decision": {
            "route": result.decision.route,
            "reason": result.decision.reason,
            "rewritten_query": result.decision.rewritten_query,
            "document_scope": result.decision.document_scope,
        },
        "answer_mode": result.answer_mode,
        "retrieval_latency_ms": result.retrieval_latency_ms,
        "knowledge_base_selection": {
            "mode": result.knowledge_base_selection.mode,
            "knowledge_base_ids": list(result.knowledge_base_selection.knowledge_base_ids),
            "resolved_count": result.knowledge_base_selection.resolved_count,
            "resolved_knowledge_base_ids": list(
                result.knowledge_base_selection.resolved_knowledge_base_ids
            ),
            "ambient_system_knowledge_base_ids": list(
                result.knowledge_base_selection.ambient_system_knowledge_base_ids
            ),
            "ambient_system_knowledge_base_count": (
                result.knowledge_base_selection.ambient_system_knowledge_base_count
            ),
        },
        "retrieval_evidence": (
            {
                "intent": evidence.intent,
                "candidate_count": evidence.candidate_count,
                "injected_count": evidence.injected_count,
                "rejected_count": evidence.rejected_count,
                "source_counts": evidence.source_counts,
                "structured_entity_types": list(evidence.structured_entity_types),
                "reranker": evidence.reranker,
                "budget_truncated": evidence.budget_truncated,
            }
            if evidence is not None
            else None
        ),
        "retrieval_attempt_count": result.retrieval_attempt_count,
        "insufficient_evidence": result.insufficient_evidence,
    }
