"""Retrieval routing and graph input helpers for conversation runs."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from langchain_core.messages import BaseMessage
from sqlalchemy.orm import Session

from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import RetrievalService, RetrievedChunk
from my_agents.knowledge.routing import (
    AnswerMode,
    RetrievalRoutingDecision,
    answer_mode_for_route,
    is_relevant_retrieval_result,
    route_retrieval,
)


@dataclass(frozen=True)
class ConversationRetrievalContext:
    """Shared retrieval-routing output for sync and streaming run paths."""

    decision: RetrievalRoutingDecision
    answer_mode: AnswerMode
    retrieved_chunks: list[RetrievedChunk]
    retrieval_latency_ms: float
    knowledge_base_selection: KnowledgeBaseSelectionContext


def prepare_retrieval_context(
    *,
    db: Session,
    user_id: str,
    message: str,
    messages: list[BaseMessage],
    selection_context: KnowledgeBaseSelectionContext,
) -> ConversationRetrievalContext:
    service = RetrievalService(db)
    selected_ids = (
        selection_context.knowledge_base_ids if selection_context.mode == "selected" else None
    )
    document_count = service.authorized_document_count(
        user_id=user_id,
        knowledge_base_ids=selected_ids,
    )
    decision = route_retrieval(
        message=message,
        history=messages,
        authorized_document_count=document_count,
    )
    if decision.route in {"no_retrieval", "clarification_required"}:
        return ConversationRetrievalContext(
            decision=decision,
            answer_mode=answer_mode_for_route(decision=decision, relevant_context_found=False),
            retrieved_chunks=[],
            retrieval_latency_ms=0.0,
            knowledge_base_selection=selection_context,
        )
    retrieval_started = perf_counter()
    retrieved_chunks = service.retrieve_scoped(
        user_id=user_id,
        query=decision.rewritten_query,
        knowledge_base_ids=selected_ids,
    )
    retrieval_latency_ms = round((perf_counter() - retrieval_started) * 1000, 3)
    relevant_context_found = any(
        is_relevant_retrieval_result(
            route=decision.route,
            source=item.source,
            score=item.score,
        )
        for item in retrieved_chunks
    )
    return ConversationRetrievalContext(
        decision=decision,
        answer_mode=answer_mode_for_route(
            decision=decision,
            relevant_context_found=relevant_context_found,
        ),
        retrieved_chunks=retrieved_chunks,
        retrieval_latency_ms=retrieval_latency_ms,
        knowledge_base_selection=selection_context,
    )


def graph_input_for_run(
    *,
    messages: list[BaseMessage],
    user_id: str,
    conversation_id: str,
    retrieval_context: ConversationRetrievalContext,
) -> dict[str, object]:
    used_chunks = chunks_used_for_answer(retrieval_context)
    return {
        "messages": messages,
        "principal_id": user_id,
        "conversation_id": conversation_id,
        "retrieved_chunk_ids": [item.chunk.id for item in used_chunks],
        "retrieved_context": retrieved_context_for_graph(used_chunks),
        "retrieval_route": retrieval_context.decision.route,
        "answer_mode": retrieval_context.answer_mode,
        "document_scope": retrieval_context.decision.document_scope,
    }


def chunks_used_for_answer(
    retrieval_context: ConversationRetrievalContext,
) -> list[RetrievedChunk]:
    if retrieval_context.answer_mode == "general_knowledge":
        return []
    return [
        item
        for item in retrieval_context.retrieved_chunks
        if is_relevant_retrieval_result(
            route=retrieval_context.decision.route,
            source=item.source,
            score=item.score,
        )
    ]


def retrieved_context_for_graph(retrieved_chunks: list[RetrievedChunk]) -> list[dict[str, object]]:
    return [
        {
            "document_id": item.document.id,
            "chunk_id": item.chunk.id,
            "title": item.document.title,
            "snippet": item.chunk.content[:800],
            "source_page": item.chunk.source_page,
            "source_filename": item.document.source_filename,
            "source": item.source,
        }
        for item in retrieved_chunks
    ]


def clarification_reply(decision: RetrievalRoutingDecision) -> str:
    return (
        "I need one more detail before using uploaded documents: which document or file "
        "should I use? I will only search documents you are authorized to access."
        f" Retrieval route: `{decision.route}`."
    )


def compose_rag_reply(
    base_reply: str,
    retrieved_chunks: list[RetrievedChunk],
    answer_mode: AnswerMode,
) -> str:
    """Return the model reply without prepending clipped retrieval snippets.

    Grounding is exposed through structured citations and graph input context. Injecting
    hard-truncated chunk prefixes into the assistant-visible answer made snippets look
    like broken assistant prose and wasted the UI's citation affordance.
    """
    return base_reply
