"""Conversation run state transitions and synchronous execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from langchain_core.messages import BaseMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.agents.agentic_rag import DeterministicAgenticRagGroundingVerifier
from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.responders import ResponseProviderConfigurationError
from my_agents.api.assistant import GraphRunner
from my_agents.api.conversations.agent_trace import conversation_agent_trace_steps
from my_agents.api.conversations.retrieval_context import (
    chunks_used_for_answer,
    clarification_request,
    compose_rag_reply,
    graph_input_for_run,
    insufficient_evidence_reply,
    log_retrieval_context_for_llm,
    prepare_retrieval_context,
)
from my_agents.api.conversations.run_events import (
    answer_composed_payload,
    append_run_event,
    graph_invoked_payload,
    retrieval_completed_payload,
    sse_event,
    user_message_stored_payload,
)
from my_agents.api.conversations.serializers import (
    coerce_route,
    completed_run_response,
    knowledge_base_selection_payload,
)
from my_agents.conversations.models import (
    AgentEventType,
    AgentRunModel,
    MessageModel,
    MessageRole,
    RunStatus,
)
from my_agents.conversations.schemas import (
    ConversationClarificationRequest,
    ConversationRunResponse,
    ConversationRunWarning,
)
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import CitationModel
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision
from my_agents.schemas import RouteDecision
from my_agents.settings import get_settings

ACTIVE_RUN_STATUSES = (RunStatus.RUNNING.value, RunStatus.CANCELLING.value)
_GROUNDING_VERIFIER = DeterministicAgenticRagGroundingVerifier()


def complete_sync_conversation_run(
    *,
    db: Session,
    conversation_id: str,
    user_id: str,
    prompt: str,
    messages: list[BaseMessage],
    run: AgentRunModel,
    selection_context: KnowledgeBaseSelectionContext,
    graph_runner: GraphRunner,
    warnings: list[ConversationRunWarning] | None = None,
) -> ConversationRunResponse:
    try:
        return _complete_sync_conversation_run(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            prompt=prompt,
            messages=messages,
            run=run,
            selection_context=selection_context,
            graph_runner=graph_runner,
            warnings=warnings,
        )
    except HTTPException:
        fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type="HTTPException",
        )
        raise
    except ResponseProviderConfigurationError as exc:
        fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="conversation run failed") from exc
    except Exception as exc:
        fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="conversation run failed") from exc


def _complete_sync_conversation_run(
    *,
    db: Session,
    conversation_id: str,
    user_id: str,
    prompt: str,
    messages: list[BaseMessage],
    run: AgentRunModel,
    selection_context: KnowledgeBaseSelectionContext,
    graph_runner: GraphRunner,
    warnings: list[ConversationRunWarning] | None = None,
) -> ConversationRunResponse:
    retrieval_context = prepare_retrieval_context(
        db=db,
        user_id=user_id,
        conversation_id=conversation_id,
        message=prompt,
        messages=messages,
        selection_context=selection_context,
    )
    record_run_retrieval_metadata(
        db,
        run.id,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        selection_context=retrieval_context.knowledge_base_selection,
    )
    append_run_event(
        db,
        run.id,
        AgentEventType.RETRIEVAL_COMPLETED,
        retrieval_completed_payload(
            retrieved_chunks=retrieval_context.retrieved_chunks,
            retrieval_latency_ms=retrieval_context.retrieval_latency_ms,
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            retrieval_evidence=retrieval_context.retrieval_evidence,
            retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
            insufficient_evidence=retrieval_context.insufficient_evidence,
        ),
    )
    if retrieval_context.decision.route == "clarification_required":
        route = classify_messages(messages)
        clarification = clarification_request(retrieval_context.decision)
        return persist_completed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            retrieved_chunks=[],
            route=route,
            reply="",
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            warnings=warnings,
            clarification=clarification,
            retrieval_evidence=retrieval_context.retrieval_evidence,
        )
    if retrieval_context.insufficient_evidence:
        route = classify_messages(messages)
        return persist_completed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            retrieved_chunks=[],
            route=route,
            reply=insufficient_evidence_reply(),
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            warnings=warnings,
            insufficient_evidence=True,
            retrieval_evidence=retrieval_context.retrieval_evidence,
        )
    graph_input = graph_input_for_run(
        messages=messages,
        user_id=user_id,
        conversation_id=conversation_id,
        retrieval_context=retrieval_context,
    )
    log_retrieval_context_for_llm(
        run_id=run.id,
        conversation_id=conversation_id,
        user_id=user_id,
        retrieval_context=retrieval_context,
        graph_input=graph_input,
    )
    append_run_event(
        db,
        run.id,
        AgentEventType.GRAPH_INVOKED,
        graph_invoked_payload(
            route=classify_messages(messages),
            messages=messages,
            retrieved_chunks=retrieval_context.retrieved_chunks,
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
        ),
    )
    try:
        result = graph_runner.invoke(graph_input)
    except ResponseProviderConfigurationError as exc:
        persist_failed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="conversation run failed") from exc
    except Exception as exc:
        persist_failed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="conversation run failed") from exc
    route = coerce_route(result["route"])
    used_chunks = chunks_used_for_answer(retrieval_context)
    reply = compose_rag_reply(result["reply"], used_chunks, retrieval_context.answer_mode)
    reply, used_chunks, completion_insufficient_evidence = _verified_grounding_or_fallback(
        reply=reply,
        cited_chunks=used_chunks,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
    )
    if is_run_cancelling(db, run.id):
        mark_run_cancelled(db, run.id)
        raise HTTPException(status_code=409, detail="conversation run cancelled")
    return persist_completed_run(
        db=db,
        run_id=run.id,
        conversation_id=conversation_id,
        retrieved_chunks=used_chunks,
        route=route,
        reply=reply,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        selection_context=retrieval_context.knowledge_base_selection,
        warnings=warnings,
        insufficient_evidence=completion_insufficient_evidence,
        retrieval_evidence=retrieval_context.retrieval_evidence,
    )


def _verified_grounding_or_fallback(
    *,
    reply: str,
    cited_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    retrieval_attempt_count: int,
) -> tuple[str, list[RetrievedChunk], bool]:
    verification = _GROUNDING_VERIFIER.verify(
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        cited_chunks=cited_chunks,
        citation_count=len(cited_chunks),
        retrieval_attempt_count=retrieval_attempt_count,
    )
    if verification.passed:
        return reply, cited_chunks, False
    if retrieval_decision.route == "retrieval_required" and retrieval_attempt_count >= 2:
        fallback_verification = _GROUNDING_VERIFIER.verify(
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            cited_chunks=[],
            citation_count=0,
            insufficient_evidence=True,
            retrieval_attempt_count=retrieval_attempt_count,
        )
        if fallback_verification.passed:
            return insufficient_evidence_reply(), [], True
    errors = "; ".join(verification.errors)
    raise RuntimeError(f"Agentic RAG grounding verification failed: {errors}")


def persist_completed_run(
    *,
    db: Session,
    run_id: str,
    conversation_id: str,
    retrieved_chunks: list[RetrievedChunk],
    route: RouteDecision,
    reply: str,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    warnings: list[ConversationRunWarning] | None = None,
    clarification: ConversationClarificationRequest | None = None,
    insufficient_evidence: bool = False,
    retrieval_evidence: RetrievalEvidence | None = None,
) -> ConversationRunResponse:
    assistant_message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT.value,
        content=reply,
    )
    db.add(assistant_message)
    db.flush()
    run = db.get(AgentRunModel, run_id)
    if run is None or run.conversation_id != conversation_id:
        raise RuntimeError("started conversation run is unavailable")
    run.status = RunStatus.COMPLETED.value
    run.route_label = route.label
    run.route_explanation = route.explanation
    run.retrieval_route = retrieval_decision.route
    run.answer_mode = answer_mode
    run.document_scope = retrieval_decision.document_scope
    run.retrieval_source_snapshot_json = _retrieval_source_snapshot_json(retrieved_chunks)
    run.assistant_message_id = assistant_message.id
    db.flush()
    citations = [
        CitationModel(
            run_id=run_id,
            document_id=item.document.id,
            chunk_id=item.chunk.id,
            snippet=item.chunk.content[:240],
        )
        for item in retrieved_chunks
    ]
    db.add_all(citations)
    append_run_event(
        db,
        run.id,
        AgentEventType.ANSWER_COMPOSED,
        answer_composed_payload(
            citation_count=len(citations),
            reply=reply,
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            selection_context=selection_context,
            clarification=clarification,
            insufficient_evidence=insufficient_evidence,
        ),
        commit=False,
    )
    db.commit()
    db.refresh(run)
    for citation in citations:
        db.refresh(citation)
    return completed_run_response(
        run=run,
        reply=reply,
        route=route,
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        selection_context=selection_context,
        citations=citations,
        retrieved_chunks=retrieved_chunks,
        warnings=warnings or [],
        clarification=clarification,
        agent_trace=conversation_agent_trace_steps(
            route=route,
            retrieved_chunks=retrieved_chunks,
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            selection_context=selection_context,
            citation_count=len(citations),
            reply=reply,
            retrieval_evidence=retrieval_evidence,
            clarification_required=clarification is not None,
        ),
    )


def _retrieval_source_snapshot_json(retrieved_chunks: list[RetrievedChunk]) -> str | None:
    if not retrieved_chunks:
        return None
    unique_sources: dict[str, dict[str, str | None]] = {}
    for item in retrieved_chunks:
        unique_sources.setdefault(
            item.document.id,
            {
                "document_id": item.document.id,
                "knowledge_base_id": item.document.knowledge_base_id,
                "source_filename": item.document.source_filename,
            },
        )
    return json.dumps(list(unique_sources.values()), sort_keys=True)


def fail_active_run(
    *,
    db: Session,
    run_id: str,
    conversation_id: str,
    error_type: str,
) -> str | None:
    try:
        db.rollback()
    except Exception:
        pass
    if not is_run_active(db, run_id):
        return None
    return persist_failed_run(
        db=db,
        run_id=run_id,
        conversation_id=conversation_id,
        error_type=error_type,
    )


def is_run_active(db: Session, run_id: str) -> bool:
    run = db.get(AgentRunModel, run_id, populate_existing=True)
    return run is not None and run.status in ACTIVE_RUN_STATUSES


def persist_failed_run(
    *,
    db: Session,
    run_id: str,
    conversation_id: str,
    error_type: str,
) -> str:
    run = db.get(AgentRunModel, run_id, populate_existing=True)
    if run is None or run.conversation_id != conversation_id:
        raise RuntimeError("started conversation run is unavailable")
    if run.status not in ACTIVE_RUN_STATUSES:
        return run.id
    run.status = RunStatus.FAILED.value
    append_run_event(
        db,
        run.id,
        AgentEventType.RUN_FAILED,
        {"safe_error_type": error_type},
        commit=False,
    )
    db.commit()
    return run.id


def assert_no_active_run(db: Session, conversation_id: str) -> None:
    cleanup_stale_active_runs(db, conversation_id)
    active_run = db.scalar(
        select(AgentRunModel.id)
        .where(
            AgentRunModel.conversation_id == conversation_id,
            AgentRunModel.status.in_(ACTIVE_RUN_STATUSES),
        )
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conversation run already active",
        )


def cleanup_stale_active_runs(db: Session, conversation_id: str) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().active_run_stale_after_seconds)
    stale_runs = db.scalars(
        select(AgentRunModel).where(
            AgentRunModel.conversation_id == conversation_id,
            AgentRunModel.status.in_(ACTIVE_RUN_STATUSES),
            AgentRunModel.created_at < cutoff,
        )
    ).all()
    for run in stale_runs:
        if run.status == RunStatus.CANCELLING.value:
            run.status = RunStatus.CANCELLED.value
            append_run_event(
                db,
                run.id,
                AgentEventType.RUN_CANCELLED,
                {
                    "run_id": run.id,
                    "conversation_id": run.conversation_id,
                    "status": RunStatus.CANCELLED.value,
                    "partial_reply_persisted": False,
                    "stale_active_run_cleanup": True,
                },
                commit=False,
            )
            continue
        run.status = RunStatus.FAILED.value
        append_run_event(
            db,
            run.id,
            AgentEventType.RUN_FAILED,
            {
                "safe_error_type": "StaleActiveRun",
                "safe_reason": "active run exceeded stale timeout",
                "stale_active_run_cleanup": True,
            },
            commit=False,
        )
    if stale_runs:
        db.commit()


def start_run(
    *,
    db: Session,
    conversation_id: str,
    user_id: str,
    user_message_id: str,
    message_content_length: int,
    selection_context: KnowledgeBaseSelectionContext,
) -> AgentRunModel:
    run = AgentRunModel(
        conversation_id=conversation_id,
        user_id=user_id,
        status=RunStatus.RUNNING.value,
        knowledge_base_selection_mode=selection_context.mode,
        selected_knowledge_base_ids_json=json.dumps(
            list(selection_context.knowledge_base_ids), sort_keys=True
        ),
        source_context_group_id=selection_context.source_context_group_id,
        mandatory_group_knowledge_base_ids_json=json.dumps(
            list(selection_context.mandatory_group_knowledge_base_ids), sort_keys=True
        ),
        optional_personal_knowledge_base_ids_json=json.dumps(
            list(selection_context.optional_personal_knowledge_base_ids), sort_keys=True
        ),
        resolved_knowledge_base_ids_json=json.dumps(
            list(selection_context.resolved_knowledge_base_ids), sort_keys=True
        ),
        resolved_knowledge_base_count=selection_context.resolved_count,
    )
    db.add(run)
    db.flush()
    append_run_event(
        db,
        run.id,
        AgentEventType.RUN_STARTED,
        {
            "run_id": run.id,
            "conversation_id": conversation_id,
            "status": run.status,
            **knowledge_base_selection_payload(selection_context),
        },
        commit=False,
    )
    append_run_event(
        db,
        run.id,
        AgentEventType.USER_MESSAGE_STORED,
        user_message_stored_payload(
            message_id=user_message_id,
            content_length=message_content_length,
        ),
        commit=False,
    )
    db.commit()
    db.refresh(run)
    return run


def record_run_retrieval_metadata(
    db: Session,
    run_id: str,
    *,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
) -> None:
    run = db.get(AgentRunModel, run_id)
    if run is None:
        raise RuntimeError("started conversation run is unavailable")
    run.retrieval_route = retrieval_decision.route
    run.answer_mode = answer_mode
    run.document_scope = retrieval_decision.document_scope
    run.knowledge_base_selection_mode = selection_context.mode
    run.selected_knowledge_base_ids_json = json.dumps(
        list(selection_context.knowledge_base_ids), sort_keys=True
    )
    run.source_context_group_id = selection_context.source_context_group_id
    run.mandatory_group_knowledge_base_ids_json = json.dumps(
        list(selection_context.mandatory_group_knowledge_base_ids), sort_keys=True
    )
    run.optional_personal_knowledge_base_ids_json = json.dumps(
        list(selection_context.optional_personal_knowledge_base_ids), sort_keys=True
    )
    run.resolved_knowledge_base_ids_json = json.dumps(
        list(selection_context.resolved_knowledge_base_ids), sort_keys=True
    )
    run.resolved_knowledge_base_count = selection_context.resolved_count
    db.commit()


def is_run_cancelling(db: Session, run_id: str) -> bool:
    run = db.get(AgentRunModel, run_id, populate_existing=True)
    return run is not None and run.status == RunStatus.CANCELLING.value


def mark_run_cancelled(db: Session, run_id: str) -> AgentRunModel:
    run = db.get(AgentRunModel, run_id, populate_existing=True)
    if run is None:
        raise RuntimeError("started conversation run is unavailable")
    if run.status != RunStatus.CANCELLED.value:
        run.status = RunStatus.CANCELLED.value
        append_run_event(
            db,
            run.id,
            AgentEventType.RUN_CANCELLED,
            {
                "run_id": run.id,
                "conversation_id": run.conversation_id,
                "status": RunStatus.CANCELLED.value,
                "partial_reply_persisted": False,
            },
            commit=False,
        )
        db.commit()
        db.refresh(run)
    return run


def cancelled_sse_event(db: Session, run_id: str) -> str:
    run = mark_run_cancelled(db, run_id)
    return sse_event(
        AgentEventType.RUN_CANCELLED.value,
        {
            "run_id": run.id,
            "conversation_id": run.conversation_id,
            "status": run.status,
            "partial_reply_persisted": False,
        },
    )
