"""Conversation run event persistence and SSE payload helpers."""

from __future__ import annotations

import json

from langchain_core.messages import BaseMessage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.api.conversations.serializers import knowledge_base_selection_payload
from my_agents.conversations.models import AgentEventModel, AgentEventType
from my_agents.conversations.schemas import AgentEventResponse, ConversationClarificationRequest
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision
from my_agents.schemas import RouteDecision


def count_retrieval_source(retrieved_chunks: list[RetrievedChunk], source: str) -> int:
    return sum(1 for chunk in retrieved_chunks if chunk.source == source)


def user_message_stored_payload(*, message_id: str, content_length: int) -> dict:
    return {"message_id": message_id, "content_length": content_length}


def retrieval_completed_payload(
    *,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_latency_ms: float,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    retrieval_evidence: RetrievalEvidence | None = None,
) -> dict:
    payload = {
        "retrieval_route": retrieval_decision.route,
        "answer_mode": answer_mode,
        "document_scope": retrieval_decision.document_scope,
        "authorized_context_count": len(retrieved_chunks),
        "semantic_vector_count": count_retrieval_source(retrieved_chunks, "semantic_vector"),
        "keyword_match_count": count_retrieval_source(retrieved_chunks, "keyword_match"),
        "document_metadata_count": count_retrieval_source(retrieved_chunks, "document_metadata"),
        "document_metadata_profile_count": count_retrieval_source(
            retrieved_chunks, "document_metadata_profile"
        ),
        "graph_expansion_count": count_retrieval_source(retrieved_chunks, "graph_expansion"),
        "fallback_count": count_retrieval_source(retrieved_chunks, "document_fallback"),
        "latency_ms": retrieval_latency_ms,
    }
    if retrieval_evidence is not None:
        payload.update(
            {
                "contextforge_intent": retrieval_evidence.intent,
                "contextforge_reranker": retrieval_evidence.reranker,
                "candidate_count": retrieval_evidence.candidate_count,
                "injected_count": retrieval_evidence.injected_count,
                "rejected_count": retrieval_evidence.rejected_count,
                "structured_entity_types": list(retrieval_evidence.structured_entity_types),
                "budget_truncated": retrieval_evidence.budget_truncated,
                "structured_entity_count": sum(
                    count
                    for source, count in retrieval_evidence.source_counts.items()
                    if source.startswith("structured_entity:")
                ),
            }
        )
    payload.update(knowledge_base_selection_payload(selection_context))
    return payload


def graph_invoked_payload(
    *,
    route: RouteDecision,
    messages: list[BaseMessage],
    retrieved_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
) -> dict:
    payload = {
        "route_label": route.label,
        "retrieval_route": retrieval_decision.route,
        "answer_mode": answer_mode,
        "document_scope": retrieval_decision.document_scope,
        "message_count": len(messages),
        "retrieved_chunk_count": len(retrieved_chunks),
    }
    payload.update(knowledge_base_selection_payload(selection_context))
    return payload


def answer_composed_payload(
    *,
    citation_count: int,
    reply: str,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    clarification: ConversationClarificationRequest | None = None,
) -> dict:
    payload = {
        "citation_count": citation_count,
        "reply_length": len(reply),
        "retrieval_route": retrieval_decision.route,
        "answer_mode": answer_mode,
        "document_scope": retrieval_decision.document_scope,
    }
    if clarification is not None:
        payload["clarification_required"] = True
        payload["clarification"] = clarification.model_dump(mode="json")
    payload.update(knowledge_base_selection_payload(selection_context))
    return payload


def append_run_event(
    db: Session,
    run_id: str,
    event_type: AgentEventType,
    payload: dict,
    *,
    commit: bool = True,
) -> AgentEventModel:
    next_sequence = (
        db.scalar(
            select(func.coalesce(func.max(AgentEventModel.sequence), 0)).where(
                AgentEventModel.run_id == run_id
            )
        )
        or 0
    ) + 1
    event = event_model(run_id, next_sequence, event_type, payload)
    db.add(event)
    if commit:
        db.commit()
    else:
        db.flush()
    return event


def event_model(
    run_id: str,
    sequence: int,
    event_type: AgentEventType,
    payload: dict,
) -> AgentEventModel:
    return AgentEventModel(
        run_id=run_id,
        sequence=sequence,
        event_type=event_type.value,
        payload_json=json.dumps(payload, sort_keys=True),
    )


def event_response(event: AgentEventModel) -> AgentEventResponse:
    return AgentEventResponse(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        payload=json.loads(event.payload_json),
    )


def sse_event(event_name: str, payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"
