"""Conversation run event persistence and SSE payload helpers."""

from __future__ import annotations

import json

from langchain_core.messages import BaseMessage
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.api.conversations.agent_trace import (
    agent_trace_payload,
    answer_agent_trace_step,
    graph_agent_trace_step,
    retrieval_agent_trace_steps,
)
from my_agents.api.conversations.serializers import knowledge_base_selection_payload
from my_agents.conversations.models import AgentEventModel, AgentEventType
from my_agents.conversations.schemas import (
    AgentEventPayload,
    AgentEventResponse,
    AgentTraceEvidence,
    AgentTraceStep,
    AgentTraceText,
    AnswerComposedAgentEventResponse,
    AnswerComposedEventPayload,
    ArtifactCreatedAgentEventResponse,
    ArtifactCreatedEventPayload,
    AttachmentsReadyAgentEventResponse,
    AttachmentsReadyEventPayload,
    ConversationClarificationRequest,
    DocumentWorkspaceStartedAgentEventResponse,
    DocumentWorkspaceStartedEventPayload,
    FullDocumentReadAgentEventResponse,
    FullDocumentReadEventPayload,
    GraphInvokedAgentEventResponse,
    GraphInvokedEventPayload,
    RetrievalCompletedAgentEventResponse,
    RetrievalCompletedEventPayload,
    RunCancelledAgentEventResponse,
    RunCancelledEventPayload,
    RunCancelRequestedAgentEventResponse,
    RunCancelRequestedEventPayload,
    RunFailedAgentEventResponse,
    RunFailedEventPayload,
    RunInterruptedAgentEventResponse,
    RunInterruptedEventPayload,
    RunResumedAgentEventResponse,
    RunResumedEventPayload,
    RunStartedAgentEventResponse,
    RunStartedEventPayload,
    UserMessageStoredAgentEventResponse,
    UserMessageStoredEventPayload,
)
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
    retrieval_attempt_count: int = 1,
    insufficient_evidence: bool = False,
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
        "retrieval_attempt_count": retrieval_attempt_count,
        "retrieval_retry_count": max(retrieval_attempt_count - 1, 0),
    }
    if insufficient_evidence:
        payload["insufficient_evidence"] = True
    payload["agent_trace"] = agent_trace_payload(
        retrieval_agent_trace_steps(
            retrieved_chunks=retrieved_chunks,
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            selection_context=selection_context,
            retrieval_evidence=retrieval_evidence,
        )
    )
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
    memory_source_snapshot_json: str | None = None,
) -> dict:
    payload = {
        "route_label": route.label,
        "retrieval_route": retrieval_decision.route,
        "answer_mode": answer_mode,
        "document_scope": retrieval_decision.document_scope,
        "message_count": len(messages),
        "retrieved_chunk_count": len(retrieved_chunks),
    }
    apply_memory_source_snapshot(payload, memory_source_snapshot_json)
    payload["agent_trace"] = agent_trace_payload(
        [
            graph_agent_trace_step(
                route=route,
                retrieved_chunks=retrieved_chunks,
                retrieval_decision=retrieval_decision,
                answer_mode=answer_mode,
                selection_context=selection_context,
            )
        ]
    )
    payload.update(knowledge_base_selection_payload(selection_context))
    return payload


def apply_memory_source_snapshot(
    payload: dict,
    memory_source_snapshot_json: str | None,
) -> dict:
    """Attach public-safe memory-source summary fields to a graph payload in place."""
    if not memory_source_snapshot_json:
        return payload
    memory_snapshot = json.loads(memory_source_snapshot_json)
    payload["memory_count"] = memory_snapshot.get("memory_count", 0)
    payload["memory_conflict_count"] = memory_snapshot.get("conflict_count", 0)
    categories = sorted(
        {
            str(memory["category"])
            for memory in memory_snapshot.get("memories", [])
            if isinstance(memory, dict) and memory.get("category")
        }
    )
    if categories:
        payload["memory_categories"] = categories
    provenance_types = sorted(
        {
            str(memory["provenance_type"])
            for memory in memory_snapshot.get("memories", [])
            if isinstance(memory, dict) and memory.get("provenance_type")
        }
    )
    if provenance_types:
        payload["memory_provenance_types"] = provenance_types
    return payload


def update_graph_invoked_event_memory_snapshot(
    db: Session,
    event: AgentEventModel | None,
    memory_source_snapshot_json: str | None,
    *,
    commit: bool = True,
) -> None:
    """Patch a persisted graph_invoked event once graph-owned recall has completed."""
    if event is None or not memory_source_snapshot_json:
        return
    payload = json.loads(event.payload_json)
    apply_memory_source_snapshot(payload, memory_source_snapshot_json)
    event.payload_json = json.dumps(payload, sort_keys=True)
    if commit:
        db.commit()
    else:
        db.flush()


def answer_composed_payload(
    *,
    citation_count: int,
    reply: str,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    clarification: ConversationClarificationRequest | None = None,
    insufficient_evidence: bool = False,
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
    if insufficient_evidence:
        payload["insufficient_evidence"] = True
    payload["agent_trace"] = agent_trace_payload(
        [
            answer_agent_trace_step(
                citation_count=citation_count,
                reply=reply,
                retrieval_decision=retrieval_decision,
                answer_mode=answer_mode,
                selection_context=selection_context,
                clarification_required=clarification is not None,
            )
        ]
    )
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
    """Return the closed, display-safe contract for one persisted event.

    Stored payloads are deliberately filtered through an event-specific allowlist.
    This keeps historical or accidentally persisted internal keys away from clients.
    """
    event_type = AgentEventType(event.event_type)
    raw_payload = json.loads(event.payload_json)
    common = {"id": event.id, "run_id": event.run_id, "sequence": event.sequence}
    match event_type:
        case AgentEventType.RUN_STARTED:
            return RunStartedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(RunStartedEventPayload, raw_payload),
            )
        case AgentEventType.USER_MESSAGE_STORED:
            return UserMessageStoredAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(UserMessageStoredEventPayload, raw_payload),
            )
        case AgentEventType.RETRIEVAL_COMPLETED:
            return RetrievalCompletedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(RetrievalCompletedEventPayload, raw_payload),
            )
        case AgentEventType.FULL_DOCUMENT_READ:
            return FullDocumentReadAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(FullDocumentReadEventPayload, raw_payload),
            )
        case AgentEventType.GRAPH_INVOKED:
            return GraphInvokedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(GraphInvokedEventPayload, raw_payload),
            )
        case AgentEventType.ATTACHMENTS_READY:
            return AttachmentsReadyAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(AttachmentsReadyEventPayload, raw_payload),
            )
        case AgentEventType.DOCUMENT_WORKSPACE_STARTED:
            return DocumentWorkspaceStartedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(DocumentWorkspaceStartedEventPayload, raw_payload),
            )
        case AgentEventType.ARTIFACT_CREATED:
            return ArtifactCreatedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(ArtifactCreatedEventPayload, raw_payload),
            )
        case AgentEventType.ANSWER_COMPOSED:
            return AnswerComposedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(AnswerComposedEventPayload, raw_payload),
            )
        case AgentEventType.RUN_CANCEL_REQUESTED:
            return RunCancelRequestedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(RunCancelRequestedEventPayload, raw_payload),
            )
        case AgentEventType.RUN_INTERRUPTED:
            return RunInterruptedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(RunInterruptedEventPayload, raw_payload),
            )
        case AgentEventType.RUN_RESUMED:
            return RunResumedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(RunResumedEventPayload, raw_payload),
            )
        case AgentEventType.RUN_CANCELLED:
            return RunCancelledAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(RunCancelledEventPayload, raw_payload),
            )
        case AgentEventType.RUN_FAILED:
            return RunFailedAgentEventResponse(
                **common,
                event_type=event_type.value,
                payload=_safe_event_payload(RunFailedEventPayload, raw_payload),
            )
    raise AssertionError(f"unhandled agent event type: {event_type}")


def _safe_event_payload[PayloadT: AgentEventPayload](
    payload_type: type[PayloadT],
    raw_payload: object,
) -> PayloadT:
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    allowed_fields = payload_type.model_fields.keys()
    filtered = {key: value for key, value in raw_payload.items() if key in allowed_fields}
    if "agent_trace" in filtered:
        filtered["agent_trace"] = _safe_agent_trace(filtered["agent_trace"])
    return payload_type.model_validate(filtered)


def _safe_agent_trace(raw_trace: object) -> list[AgentTraceStep]:
    if not isinstance(raw_trace, list):
        return []
    safe_steps: list[AgentTraceStep] = []
    for raw_step in raw_trace:
        if not isinstance(raw_step, dict):
            continue
        filtered_step = {
            key: value for key, value in raw_step.items() if key in AgentTraceStep.model_fields
        }
        raw_evidence = filtered_step.get("evidence")
        if isinstance(raw_evidence, dict):
            filtered_step["evidence"] = {
                key: value
                for key, value in raw_evidence.items()
                if key in AgentTraceEvidence.model_fields
            }
        for text_field in ("title", "description"):
            raw_text = filtered_step.get(text_field)
            if isinstance(raw_text, dict):
                filtered_step[text_field] = {
                    key: value
                    for key, value in raw_text.items()
                    if key in AgentTraceText.model_fields
                }
        try:
            safe_steps.append(AgentTraceStep.model_validate(filtered_step))
        except ValidationError:
            continue
    return safe_steps


def sse_event(event_name: str, payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"
