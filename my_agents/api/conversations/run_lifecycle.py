"""Conversation run state transitions and synchronous execution."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter

from fastapi import HTTPException, status
from langchain_core.messages import BaseMessage
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from my_agents.agent_runtime.citation_attribution import answer_supported_source_indices
from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.graph import GRAPH_VERSION
from my_agents.agents.general_assistant.responders import ResponseProviderConfigurationError
from my_agents.agents.rag_agent import DeterministicRagAgentGroundingVerifier
from my_agents.api.assistant import GraphRunner
from my_agents.api.conversations.agent_trace import conversation_agent_trace_steps
from my_agents.api.conversations.graph_invocation import (
    GraphRunnerExecutionError,
    graph_context_for_run,
    invoke_graph_runner_collecting_updates,
    invoke_graph_runner_resume_collecting_updates,
)
from my_agents.api.conversations.interactions import (
    delete_checkpoint_thread,
    graph_interrupt_payload,
    persist_waiting_document_selection,
)
from my_agents.api.conversations.retrieval_context import (
    ConversationRetrievalContext,
    chunks_consulted_for_answer,
    clarification_reply,
    clarification_request,
    compose_rag_reply,
    document_coverage_from_graph_state,
    graph_has_retrieval_context,
    graph_input_for_run,
    graph_memory_source_snapshot_json,
    insufficient_evidence_reply,
    log_retrieval_context_for_llm,
    retrieval_context_from_graph_state,
)
from my_agents.api.conversations.run_events import (
    answer_composed_payload,
    append_run_event,
    graph_invoked_payload,
    retrieval_completed_payload,
    sse_event,
    update_graph_invoked_event_memory_snapshot,
    user_message_stored_payload,
)
from my_agents.api.conversations.serializers import (
    coerce_route,
    completed_run_response,
    knowledge_base_selection_payload,
    user_visible_citation_pairs,
)
from my_agents.api.errors import APIErrorCode, APIHTTPException
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
    ConversationRunResult,
    ConversationRunWarning,
    DocumentCoverageResponse,
)
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import CitationModel
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision
from my_agents.observability.metrics import observe_conversation_run
from my_agents.schemas import RouteDecision
from my_agents.settings import ReasoningEffort, ReasoningMode, get_settings

ACTIVE_RUN_STATUSES = (
    RunStatus.RUNNING.value,
    RunStatus.WAITING_FOR_INPUT.value,
    RunStatus.CANCELLING.value,
)
_GROUNDING_VERIFIER = DeterministicRagAgentGroundingVerifier()
_CITATION_ATTRIBUTION_VERSION = 1
logger = logging.getLogger(__name__)


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
    document_workspace_runtime: object | None = None,
    warnings: list[ConversationRunWarning] | None = None,
    hitl_wait_seconds: int = 86_400,
    document_selection_hitl_allowed: bool = True,
    preselected_document_id: str | None = None,
) -> ConversationRunResult:
    try:
        result = _complete_sync_conversation_run(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            prompt=prompt,
            messages=messages,
            run=run,
            selection_context=selection_context,
            graph_runner=graph_runner,
            document_workspace_runtime=document_workspace_runtime,
            warnings=warnings,
            hitl_wait_seconds=hitl_wait_seconds,
            document_selection_hitl_allowed=document_selection_hitl_allowed,
            preselected_document_id=preselected_document_id,
        )
        if isinstance(result, ConversationRunResponse):
            delete_checkpoint_thread(graph_runner, run.id)
        return result
    except HTTPException:
        fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type="HTTPException",
        )
        delete_checkpoint_thread(graph_runner, run.id)
        raise
    except ResponseProviderConfigurationError as exc:
        fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        delete_checkpoint_thread(graph_runner, run.id)
        raise APIHTTPException(
            status_code=503,
            detail="conversation run failed",
            code=APIErrorCode.CONVERSATION_RUN_FAILED,
        ) from exc
    except Exception as exc:
        fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        delete_checkpoint_thread(graph_runner, run.id)
        raise APIHTTPException(
            status_code=502,
            detail="conversation run failed",
            code=APIErrorCode.CONVERSATION_RUN_FAILED,
        ) from exc


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
    document_workspace_runtime: object | None = None,
    warnings: list[ConversationRunWarning] | None = None,
    hitl_wait_seconds: int = 86_400,
    document_selection_hitl_allowed: bool = True,
    preselected_document_id: str | None = None,
) -> ConversationRunResult:
    run_started = perf_counter()
    retrieval_route = "unknown"
    answer_mode = "unknown"

    def record_run_metric(outcome: str) -> None:
        observe_conversation_run(
            mode="sync",
            outcome=outcome,
            retrieval_route=retrieval_route,
            answer_mode=answer_mode,
            duration_seconds=perf_counter() - run_started,
        )

    graph_input = graph_input_for_run(
        messages=messages,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run.id,
        document_selection_hitl_allowed=document_selection_hitl_allowed,
        preselected_document_id=preselected_document_id,
    )
    graph_context = graph_context_for_run(
        db=db,
        user_id=user_id,
        selection_context=selection_context,
        document_workspace_runtime=document_workspace_runtime,
        reasoning_mode=run.reasoning_mode,  # type: ignore[arg-type]
        reasoning_effort=run.reasoning_effort,  # type: ignore[arg-type]
    )
    try:
        result = invoke_graph_runner_collecting_updates(
            graph_runner=graph_runner,
            graph_input=graph_input,
            graph_context=graph_context,
        )
    except GraphRunnerExecutionError as exc:
        partial_state = exc.partial_state
        memory_source_snapshot = graph_memory_source_snapshot_json(partial_state)
        if graph_has_retrieval_context(partial_state):
            retrieval_context = retrieval_context_from_graph_state(partial_state, db)
            retrieval_route = retrieval_context.decision.route
            answer_mode = retrieval_context.answer_mode
            record_retrieval_completed_event(db, run.id, retrieval_context)
            if not _retrieval_halts_before_response(retrieval_context):
                append_run_event(
                    db,
                    run.id,
                    AgentEventType.GRAPH_INVOKED,
                    graph_invoked_payload(
                        route=_route_from_graph_state(partial_state, messages),
                        messages=messages,
                        retrieved_chunks=retrieval_context.retrieved_chunks,
                        retrieval_decision=retrieval_context.decision,
                        answer_mode=retrieval_context.answer_mode,
                        selection_context=retrieval_context.knowledge_base_selection,
                        memory_source_snapshot_json=memory_source_snapshot,
                    ),
                )
        original = exc.original_exception
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if isinstance(original, ResponseProviderConfigurationError)
            else status.HTTP_502_BAD_GATEWAY
        )
        persist_failed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(original).__name__,
            memory_source_snapshot=memory_source_snapshot,
        )
        record_run_metric("failed")
        raise APIHTTPException(
            status_code=status_code,
            detail="conversation run failed",
            code=APIErrorCode.CONVERSATION_RUN_FAILED,
        ) from original
    except ResponseProviderConfigurationError as exc:
        persist_failed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        record_run_metric("failed")
        raise APIHTTPException(
            status_code=503,
            detail="conversation run failed",
            code=APIErrorCode.CONVERSATION_RUN_FAILED,
        ) from exc
    except Exception as exc:
        persist_failed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        record_run_metric("failed")
        raise APIHTTPException(
            status_code=502,
            detail="conversation run failed",
            code=APIErrorCode.CONVERSATION_RUN_FAILED,
        ) from exc

    retrieval_context = retrieval_context_from_graph_state(result, db)
    retrieval_route = retrieval_context.decision.route
    answer_mode = retrieval_context.answer_mode
    record_retrieval_completed_event(db, run.id, retrieval_context)
    if graph_interrupt_payload(result) is not None:
        route = classify_messages(messages)
        run.route_label = route.label
        run.route_explanation = route.explanation
        run.retrieval_route = retrieval_context.decision.route
        run.answer_mode = retrieval_context.answer_mode
        run.document_scope = retrieval_context.decision.document_scope
        response = persist_waiting_document_selection(
            db=db,
            run=run,
            graph_state=result,
            wait_seconds=hitl_wait_seconds,
        )
        record_run_metric("waiting_for_input")
        return response
    if retrieval_context.decision.route == "clarification_required":
        route = classify_messages(messages)
        clarification = clarification_request(retrieval_context.decision)
        response = persist_completed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            consulted_chunks=[],
            route=route,
            reply=clarification_reply(result.get("reply"), retrieval_context.decision),
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            warnings=warnings,
            clarification=clarification,
            retrieval_evidence=retrieval_context.retrieval_evidence,
        )
        record_run_metric("clarification")
        return response
    if retrieval_context.insufficient_evidence:
        route = classify_messages(messages)
        response = persist_completed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            consulted_chunks=[],
            route=route,
            reply=insufficient_evidence_reply(),
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            warnings=warnings,
            insufficient_evidence=True,
            retrieval_evidence=retrieval_context.retrieval_evidence,
        )
        record_run_metric("insufficient_evidence")
        return response
    graph_state_for_logging = {**graph_input, **result}
    log_retrieval_context_for_llm(
        run_id=run.id,
        conversation_id=conversation_id,
        user_id=user_id,
        retrieval_context=retrieval_context,
        graph_input=graph_state_for_logging,
    )
    route = coerce_route(result["route"])
    memory_source_snapshot = graph_memory_source_snapshot_json(result)
    graph_event = append_run_event(
        db,
        run.id,
        AgentEventType.GRAPH_INVOKED,
        graph_invoked_payload(
            route=route,
            messages=messages,
            retrieved_chunks=retrieval_context.retrieved_chunks,
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            memory_source_snapshot_json=memory_source_snapshot,
        ),
    )
    update_graph_invoked_event_memory_snapshot(db, graph_event, memory_source_snapshot)
    consulted_chunks = chunks_consulted_for_answer(retrieval_context)
    document_coverage = document_coverage_from_graph_state(result)
    reply = compose_rag_reply(result["reply"], consulted_chunks, retrieval_context.answer_mode)
    reply, consulted_chunks, completion_insufficient_evidence = _verified_grounding_or_fallback(
        reply=reply,
        consulted_chunks=consulted_chunks,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
    )
    if is_run_cancelling(db, run.id):
        mark_run_cancelled(db, run.id)
        record_run_metric("cancelled")
        raise HTTPException(status_code=409, detail="conversation run cancelled")
    response = persist_completed_run(
        db=db,
        run_id=run.id,
        conversation_id=conversation_id,
        consulted_chunks=consulted_chunks,
        route=route,
        reply=reply,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        selection_context=retrieval_context.knowledge_base_selection,
        warnings=warnings,
        insufficient_evidence=completion_insufficient_evidence,
        retrieval_evidence=retrieval_context.retrieval_evidence,
        memory_source_snapshot=memory_source_snapshot,
        document_coverage=document_coverage,
        retrieval_latency_ms=retrieval_context.retrieval_latency_ms,
    )
    record_run_metric("completed")
    return response


def complete_resumed_conversation_run(
    *,
    db: Session,
    run: AgentRunModel,
    messages: list[BaseMessage],
    selection_context: KnowledgeBaseSelectionContext,
    selected_document_id: str,
    graph_runner: GraphRunner,
    hitl_wait_seconds: int,
) -> ConversationRunResult:
    """Resume one document-selection checkpoint and finalize its Product DB run."""
    graph_context = graph_context_for_run(
        db=db,
        user_id=run.user_id,
        selection_context=selection_context,
        reasoning_mode=run.reasoning_mode,  # type: ignore[arg-type]
        reasoning_effort=run.reasoning_effort,  # type: ignore[arg-type]
    )
    try:
        result = invoke_graph_runner_resume_collecting_updates(
            graph_runner=graph_runner,
            run_id=run.id,
            resume_value={"document_id": selected_document_id},
            graph_context=graph_context,
        )
    except GraphRunnerExecutionError as exc:
        logger.warning(
            "conversation_run.resume_failed run_id=%s error_class=%s",
            run.id,
            type(exc.original_exception).__name__,
        )
        persist_failed_run(
            db=db,
            run_id=run.id,
            conversation_id=run.conversation_id,
            error_type=type(exc.original_exception).__name__,
        )
        delete_checkpoint_thread(graph_runner, run.id)
        raise APIHTTPException(
            status_code=502,
            detail="conversation run resume failed",
            code=APIErrorCode.CONVERSATION_RUN_FAILED,
        ) from exc.original_exception

    retrieval_context = retrieval_context_from_graph_state(result, db)
    record_retrieval_completed_event(db, run.id, retrieval_context)
    if graph_interrupt_payload(result) is not None:
        return persist_waiting_document_selection(
            db=db,
            run=run,
            graph_state=result,
            wait_seconds=hitl_wait_seconds,
        )
    route = coerce_route(result.get("route") or classify_messages(messages))
    if retrieval_context.insufficient_evidence or "reply" not in result:
        response = persist_completed_run(
            db=db,
            run_id=run.id,
            conversation_id=run.conversation_id,
            consulted_chunks=[],
            route=route,
            reply=insufficient_evidence_reply(),
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=selection_context,
            insufficient_evidence=True,
            retrieval_evidence=retrieval_context.retrieval_evidence,
        )
        delete_checkpoint_thread(graph_runner, run.id)
        return response

    memory_source_snapshot = graph_memory_source_snapshot_json(result)
    graph_event = append_run_event(
        db,
        run.id,
        AgentEventType.GRAPH_INVOKED,
        graph_invoked_payload(
            route=route,
            messages=messages,
            retrieved_chunks=retrieval_context.retrieved_chunks,
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=selection_context,
            memory_source_snapshot_json=memory_source_snapshot,
        ),
    )
    update_graph_invoked_event_memory_snapshot(db, graph_event, memory_source_snapshot)
    consulted_chunks = chunks_consulted_for_answer(retrieval_context)
    document_coverage = document_coverage_from_graph_state(result)
    reply = compose_rag_reply(str(result["reply"]), consulted_chunks, retrieval_context.answer_mode)
    reply, consulted_chunks, insufficient = _verified_grounding_or_fallback(
        reply=reply,
        consulted_chunks=consulted_chunks,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
    )
    response = persist_completed_run(
        db=db,
        run_id=run.id,
        conversation_id=run.conversation_id,
        consulted_chunks=consulted_chunks,
        route=route,
        reply=reply,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        selection_context=selection_context,
        insufficient_evidence=insufficient,
        retrieval_evidence=retrieval_context.retrieval_evidence,
        memory_source_snapshot=memory_source_snapshot,
        document_coverage=document_coverage,
        retrieval_latency_ms=retrieval_context.retrieval_latency_ms,
    )
    delete_checkpoint_thread(graph_runner, run.id)
    return response


def _verified_grounding_or_fallback(
    *,
    reply: str,
    consulted_chunks: list[RetrievedChunk],
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    retrieval_attempt_count: int,
) -> tuple[str, list[RetrievedChunk], bool]:
    verification = _GROUNDING_VERIFIER.verify(
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        consulted_chunks=consulted_chunks,
        consulted_count=len(consulted_chunks),
        retrieval_attempt_count=retrieval_attempt_count,
    )
    if verification.passed:
        return reply, consulted_chunks, False
    if retrieval_decision.route == "retrieval_required" and retrieval_attempt_count >= 2:
        fallback_verification = _GROUNDING_VERIFIER.verify(
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            consulted_chunks=[],
            consulted_count=0,
            insufficient_evidence=True,
            retrieval_attempt_count=retrieval_attempt_count,
        )
        if fallback_verification.passed:
            return insufficient_evidence_reply(), [], True
    errors = "; ".join(verification.errors)
    raise RuntimeError(f"RAG Agent grounding verification failed: {errors}")


def persist_completed_run(
    *,
    db: Session,
    run_id: str,
    conversation_id: str,
    consulted_chunks: list[RetrievedChunk],
    route: RouteDecision,
    reply: str,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    warnings: list[ConversationRunWarning] | None = None,
    clarification: ConversationClarificationRequest | None = None,
    insufficient_evidence: bool = False,
    retrieval_evidence: RetrievalEvidence | None = None,
    memory_source_snapshot: str | None = None,
    document_coverage: DocumentCoverageResponse | None = None,
    retrieval_latency_ms: float = 0.0,
) -> ConversationRunResponse:
    from my_agents.document_workspace.service import artifacts_for_run, attachments_for_run

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
    run.retrieval_source_snapshot_json = _retrieval_source_snapshot_json(consulted_chunks)
    run.memory_source_snapshot_json = memory_source_snapshot
    run.citation_attribution_version = _CITATION_ATTRIBUTION_VERSION
    run.assistant_message_id = assistant_message.id
    run.interaction_id = None
    run.interaction_type = None
    run.interaction_payload_json = None
    run.interaction_expires_at = None
    db.flush()
    answer_supported_indexes = set(
        answer_supported_source_indices(
            reply=reply,
            # Attribute only against the evidence text the API actually discloses. A match
            # outside the persisted 240-character snippet would be unverifiable in the panel.
            source_texts=[item.chunk.content[:240] for item in consulted_chunks],
        )
    )
    citations = [
        CitationModel(
            run_id=run_id,
            document_id=item.document.id,
            chunk_id=item.chunk.id,
            snippet=item.chunk.content[:240],
            used_in_answer=index in answer_supported_indexes,
        )
        for index, item in enumerate(consulted_chunks)
    ]
    db.add_all(citations)
    visible_consulted_pairs = user_visible_citation_pairs(
        citations, consulted_chunks, selection_context=selection_context
    )
    visible_used_pairs = [
        pair for pair in visible_consulted_pairs if pair[0].used_in_answer is True
    ]
    visible_citations = [citation for citation, _ in visible_used_pairs]
    visible_consulted_chunks = [item for _, item in visible_consulted_pairs]
    if document_coverage is not None:
        append_run_event(
            db,
            run.id,
            AgentEventType.FULL_DOCUMENT_READ,
            {
                **document_coverage.model_dump(mode="json"),
                "latency_ms": retrieval_latency_ms,
            },
            commit=False,
        )
    append_run_event(
        db,
        run.id,
        AgentEventType.ANSWER_COMPOSED,
        answer_composed_payload(
            citation_count=len(visible_citations),
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
        db=db,
        run=run,
        reply=reply,
        route=route,
        retrieval_decision=retrieval_decision,
        answer_mode=answer_mode,
        selection_context=selection_context,
        citations=citations,
        consulted_chunks=consulted_chunks,
        warnings=warnings or [],
        clarification=clarification,
        agent_trace=conversation_agent_trace_steps(
            route=route,
            retrieved_chunks=visible_consulted_chunks,
            retrieval_decision=retrieval_decision,
            answer_mode=answer_mode,
            selection_context=selection_context,
            citation_count=len(visible_citations),
            reply=reply,
            retrieval_evidence=retrieval_evidence,
            clarification_required=clarification is not None,
        ),
        attachments=attachments_for_run(db, run.id),
        artifacts=artifacts_for_run(db, run.id),
        document_coverage=document_coverage,
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
    memory_source_snapshot: str | None = None,
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
        memory_source_snapshot=memory_source_snapshot,
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
    memory_source_snapshot: str | None = None,
) -> str:
    run = db.get(AgentRunModel, run_id, populate_existing=True)
    if run is None or run.conversation_id != conversation_id:
        raise RuntimeError("started conversation run is unavailable")
    if run.status not in ACTIVE_RUN_STATUSES:
        return run.id
    run.status = RunStatus.FAILED.value
    run.interaction_id = None
    run.interaction_type = None
    run.interaction_payload_json = None
    run.interaction_expires_at = None
    if memory_source_snapshot is not None:
        run.memory_source_snapshot_json = memory_source_snapshot
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
        raise APIHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conversation run already active",
            code=APIErrorCode.CONVERSATION_RUN_ALREADY_ACTIVE,
        )


def cleanup_stale_active_runs(db: Session, conversation_id: str) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=get_settings().active_run_stale_after_seconds)
    stale_runs = db.scalars(
        select(AgentRunModel).where(
            AgentRunModel.conversation_id == conversation_id,
            or_(
                (
                    AgentRunModel.status.in_((RunStatus.RUNNING.value, RunStatus.CANCELLING.value))
                    & (AgentRunModel.created_at < cutoff)
                ),
                (
                    (AgentRunModel.status == RunStatus.WAITING_FOR_INPUT.value)
                    & (AgentRunModel.interaction_expires_at.is_not(None))
                    & (AgentRunModel.interaction_expires_at <= now)
                ),
            ),
        )
    ).all()
    for run in stale_runs:
        if run.status in {RunStatus.CANCELLING.value, RunStatus.WAITING_FOR_INPUT.value}:
            run.status = RunStatus.CANCELLED.value
            run.interaction_id = None
            run.interaction_type = None
            run.interaction_payload_json = None
            run.interaction_expires_at = None
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
    reasoning_mode: ReasoningMode,
    reasoning_effort: ReasoningEffort,
) -> AgentRunModel:
    run = AgentRunModel(
        conversation_id=conversation_id,
        user_id=user_id,
        status=RunStatus.RUNNING.value,
        graph_version=GRAPH_VERSION,
        reasoning_mode=reasoning_mode,
        reasoning_effort=reasoning_effort,
        knowledge_base_selection_mode=selection_context.mode,
        selected_knowledge_base_ids_json=json.dumps(
            list(selection_context.knowledge_base_ids), sort_keys=True
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
            "reasoning_mode": reasoning_mode,
            "reasoning_effort": reasoning_effort,
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
    run.resolved_knowledge_base_ids_json = json.dumps(
        list(selection_context.resolved_knowledge_base_ids), sort_keys=True
    )
    run.resolved_knowledge_base_count = selection_context.resolved_count
    db.commit()


def record_retrieval_completed_event(
    db: Session,
    run_id: str,
    retrieval_context: ConversationRetrievalContext,
) -> dict[str, object]:
    """Persist retrieval metadata and redacted retrieval_completed event payload."""
    record_run_retrieval_metadata(
        db,
        run_id,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        selection_context=retrieval_context.knowledge_base_selection,
    )
    payload = retrieval_completed_payload(
        retrieved_chunks=retrieval_context.retrieved_chunks,
        retrieval_latency_ms=retrieval_context.retrieval_latency_ms,
        retrieval_decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        selection_context=retrieval_context.knowledge_base_selection,
        retrieval_evidence=retrieval_context.retrieval_evidence,
        retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
        insufficient_evidence=retrieval_context.insufficient_evidence,
    )
    append_run_event(
        db,
        run_id,
        AgentEventType.RETRIEVAL_COMPLETED,
        payload,
    )
    return payload


def _retrieval_halts_before_response(retrieval_context: ConversationRetrievalContext) -> bool:
    return (
        retrieval_context.decision.route == "clarification_required"
        or retrieval_context.insufficient_evidence
    )


def _route_from_graph_state(
    graph_state: dict[str, object],
    messages: list[BaseMessage],
) -> RouteDecision:
    route = graph_state.get("route")
    if route is not None:
        return coerce_route(route)
    return classify_messages(messages)


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
