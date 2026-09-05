"""Assistant-message replay endpoint."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.responders import ResponseProviderConfigurationError
from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.graph_invocation import graph_context_for_run
from my_agents.api.conversations.graph_streaming import fallback_answer_deltas, stream_graph_items
from my_agents.api.conversations.interactions import delete_checkpoint_thread
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
    sse_event,
    update_graph_invoked_event_memory_snapshot,
    user_message_stored_payload,
)
from my_agents.api.conversations.run_lifecycle import (
    _verified_grounding_or_fallback,
    admit_run,
    assert_no_active_run,
    complete_sync_conversation_run,
    fail_active_run,
    is_run_active,
    persist_completed_run,
    persist_failed_run,
    reasoning_summary_items_from_graph_state,
    record_retrieval_completed_event,
)
from my_agents.api.conversations.serializers import (
    coerce_route,
    knowledge_base_selection_payload,
    run_knowledge_base_selection,
)
from my_agents.api.conversations.sse_contract import conversation_sse_responses
from my_agents.api.conversations.transcripts import (
    base_messages_from_persisted,
    persisted_messages_for_conversation,
    preceding_user_message,
    prune_conversation_from_message,
    run_for_assistant_message,
)
from my_agents.api.reasoning import resolve_reasoning_preferences
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_can_send_prompt
from my_agents.conversations.models import (
    AgentEventModel,
    AgentEventType,
    AgentRunModel,
    MessageModel,
    MessageRole,
)
from my_agents.conversations.schemas import (
    ConversationReplayRequest,
    ConversationRunResponse,
    ConversationRunWarning,
)
from my_agents.knowledge.auth import (
    KnowledgeBaseSelectionContext,
    resolve_conversation_knowledge_context,
)
from my_agents.knowledge.models import DocumentModel
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.knowledge.schemas import KnowledgeBaseSelection
from my_agents.persistence.database import get_database_session
from my_agents.settings import ReasoningEffort, ReasoningMode, Settings, get_settings

router = APIRouter()


@dataclass(frozen=True)
class ReplayContext:
    target_message: MessageModel
    removed_messages: list[MessageModel]
    prefix_messages: list[MessageModel]
    preceding_message: MessageModel
    original_run: AgentRunModel | None
    replay_warnings: list[ConversationRunWarning]
    selection_context: KnowledgeBaseSelectionContext
    reasoning_mode: ReasoningMode
    reasoning_effort: ReasoningEffort
    preselected_document_id: str | None


@router.post(
    "/{conversation_id}/messages/{message_id}/replay",
    response_model=ConversationRunResponse,
)
def replay_assistant_message(
    conversation_id: str,
    message_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Annotated[ConversationReplayRequest, Body(default_factory=ConversationReplayRequest)],
) -> ConversationRunResponse:
    """Regenerate an existing assistant message from the transcript prefix before it.

    V1 keeps the transcript linear after success: the target assistant message
    and all later messages, runs, events, and citations are pruned only after the
    fresh run completes. This keeps the old assistant answer visible if replay is
    interrupted by a backend restart or provider failure.
    """
    replay_context = replay_context_for_request(
        db,
        conversation_id=conversation_id,
        message_id=message_id,
        principal=principal,
        settings=settings,
        request=request,
    )
    run = admit_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        existing_user_message=replay_context.preceding_message,
        selection_context=replay_context.selection_context,
        reasoning_mode=replay_context.reasoning_mode,
        reasoning_effort=replay_context.reasoning_effort,
    ).run
    messages = base_messages_from_persisted(replay_context.prefix_messages)
    response = complete_sync_conversation_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        prompt=replay_context.preceding_message.content,
        messages=messages,
        run=run,
        selection_context=replay_context.selection_context,
        graph_runner=graph_runner,
        warnings=replay_context.replay_warnings,
        document_selection_hitl_allowed=False,
        preselected_document_id=replay_context.preselected_document_id,
    )
    prune_conversation_from_message(
        db,
        conversation_id=conversation_id,
        target_message=replay_context.target_message,
        removed_messages=replay_context.removed_messages,
        original_run=replay_context.original_run,
        preserved_run_ids={run.id},
    )
    return response


@router.post(
    "/{conversation_id}/messages/{message_id}/replay/stream",
    responses=conversation_sse_responses(
        "Server-Sent Events stream for replaying one assistant message."
    ),
)
def stream_replay_assistant_message(
    conversation_id: str,
    message_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Annotated[ConversationReplayRequest, Body(default_factory=ConversationReplayRequest)],
) -> StreamingResponse:
    """Stream regeneration of an existing assistant message.

    The old assistant answer and later transcript rows are pruned only after the
    streamed replay completes successfully. Stream failures persist a redacted failed run
    but preserve the existing transcript.
    """
    replay_context = replay_context_for_request(
        db,
        conversation_id=conversation_id,
        message_id=message_id,
        principal=principal,
        settings=settings,
        request=request,
    )
    run = admit_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        existing_user_message=replay_context.preceding_message,
        selection_context=replay_context.selection_context,
        reasoning_mode=replay_context.reasoning_mode,
        reasoning_effort=replay_context.reasoning_effort,
    ).run
    return StreamingResponse(
        replay_conversation_run_events(
            db=db,
            run=run,
            conversation_id=conversation_id,
            user_id=principal.user_id,
            replay_context=replay_context,
            graph_runner=graph_runner,
        ),
        media_type="text/event-stream",
    )


def replay_context_for_request(
    db: Session,
    *,
    conversation_id: str,
    message_id: str,
    principal: Principal,
    settings: Settings,
    request: ConversationReplayRequest,
) -> ReplayContext:
    assert_guest_can_send_prompt(db, principal, settings)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    assert_no_active_run(db, conversation_id)
    target_message = db.get(MessageModel, message_id)
    if target_message is None or target_message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message not found")
    if target_message.role != MessageRole.ASSISTANT.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="message is not an assistant message",
        )

    persisted_messages = persisted_messages_for_conversation(db, conversation_id)
    try:
        target_index = next(
            index for index, message in enumerate(persisted_messages) if message.id == message_id
        )
    except StopIteration as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="message not found"
        ) from exc
    prefix_messages = persisted_messages[:target_index]
    preceding_message = preceding_user_message(prefix_messages)
    if preceding_message is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="assistant message has no preceding user prompt",
        )

    original_run = run_for_assistant_message(db, conversation_id, message_id)
    requested_selection = (
        run_knowledge_base_selection(original_run)
        if original_run is not None
        else request.knowledge_base_selection or KnowledgeBaseSelection()
    )
    reasoning = resolve_reasoning_preferences(
        settings=settings,
        principal=principal,
        requested_mode=request.reasoning_mode,
        requested_effort=request.reasoning_effort,
        uses_document_workspace=False,
        fallback_mode=(original_run.reasoning_mode if original_run is not None else None),
        fallback_effort=(original_run.reasoning_effort if original_run is not None else None),
    )
    original_full_document = _full_document_source_for_run(db, original_run)
    try:
        selection_context = resolve_conversation_knowledge_context(
            db,
            principal=principal,
            requested_selection=requested_selection,
        )
    except HTTPException:
        if original_full_document is None:
            raise
        selection_context = resolve_conversation_knowledge_context(
            db,
            principal=principal,
            requested_selection=KnowledgeBaseSelection(),
        )
    replay_warnings = source_warnings_for_replay(db, original_run)
    preselected_document_id = None
    if original_full_document is not None:
        original_document_id = original_full_document["document_id"]
        preselected_document_id = original_document_id
        if not RetrievalService(db).document_is_user_selectable(
            user_id=principal.user_id,
            document_id=original_document_id,
            knowledge_base_ids=selection_context.retrieval_knowledge_base_ids,
        ):
            replay_warnings = _with_unavailable_full_document_warning(
                replay_warnings,
                document_id=original_document_id,
                source_filename=original_full_document.get("source_filename"),
            )
    return ReplayContext(
        target_message=target_message,
        removed_messages=persisted_messages[target_index:],
        prefix_messages=prefix_messages,
        preceding_message=preceding_message,
        original_run=original_run,
        replay_warnings=replay_warnings,
        selection_context=selection_context,
        reasoning_mode=reasoning.mode,
        reasoning_effort=reasoning.effort,
        preselected_document_id=preselected_document_id,
    )


def replay_conversation_run_events(
    *,
    db: Session,
    conversation_id: str,
    user_id: str,
    replay_context: ReplayContext,
    graph_runner: GraphRunner,
    run: AgentRunModel,
) -> Iterator[str]:
    try:
        yield sse_event(
            AgentEventType.RUN_STARTED.value,
            {
                "run_id": run.id,
                "conversation_id": conversation_id,
                "status": run.status,
                "reasoning_mode": replay_context.reasoning_mode,
                "reasoning_effort": replay_context.reasoning_effort,
                **knowledge_base_selection_payload(replay_context.selection_context),
            },
        )
        yield sse_event(
            AgentEventType.USER_MESSAGE_STORED.value,
            user_message_stored_payload(
                message_id=replay_context.preceding_message.id,
                content_length=len(replay_context.preceding_message.content.strip()),
            ),
        )
        messages = base_messages_from_persisted(replay_context.prefix_messages)
        graph_input = graph_input_for_run(
            messages=messages,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run.id,
            document_selection_hitl_allowed=False,
            preselected_document_id=replay_context.preselected_document_id,
        )
        graph_context = graph_context_for_run(
            db=db,
            user_id=user_id,
            selection_context=replay_context.selection_context,
            reasoning_mode=replay_context.reasoning_mode,
            reasoning_effort=replay_context.reasoning_effort,
        )
        retrieval_context: ConversationRetrievalContext | None = None
        memory_snapshot = graph_memory_source_snapshot_json(graph_input)
        stream_route = classify_messages(messages)
        graph_invoked = False
        graph_event = None
        delta_sequence = 0
        reasoning_delta_sequences: dict[str, int] = {}
        streamed_base_reply_parts: list[str] = []
        result: dict[str, Any] | None = None
        try:
            for item in stream_graph_items(
                graph_runner=graph_runner,
                graph_input=graph_input,
                graph_context=graph_context,
            ):
                if item.kind == "update":
                    if item.result:
                        if retrieval_context is None and graph_has_retrieval_context(item.result):
                            retrieval_context = retrieval_context_from_graph_state(item.result, db)
                            retrieval_payload = record_retrieval_completed_event(
                                db, run.id, retrieval_context
                            )
                            yield sse_event(
                                AgentEventType.RETRIEVAL_COMPLETED.value,
                                retrieval_payload,
                            )
                        memory_snapshot = (
                            graph_memory_source_snapshot_json(item.result) or memory_snapshot
                        )
                    if memory_snapshot and not graph_invoked and retrieval_context is not None:
                        graph_payload = graph_invoked_payload(
                            route=stream_route,
                            messages=messages,
                            retrieved_chunks=retrieval_context.retrieved_chunks,
                            retrieval_decision=retrieval_context.decision,
                            answer_mode=retrieval_context.answer_mode,
                            selection_context=retrieval_context.knowledge_base_selection,
                            memory_source_snapshot_json=memory_snapshot,
                        )
                        graph_event = append_run_event(
                            db, run.id, AgentEventType.GRAPH_INVOKED, graph_payload
                        )
                        yield sse_event(AgentEventType.GRAPH_INVOKED.value, graph_payload)
                        graph_invoked = True
                    continue
                if item.kind == "reasoning_delta" and item.stage:
                    sequence = reasoning_delta_sequences.get(item.stage, 0) + 1
                    reasoning_delta_sequences[item.stage] = sequence
                    yield sse_event(
                        "reasoning_summary_delta",
                        {"stage": item.stage, "delta": item.delta, "sequence": sequence},
                    )
                    continue
                if (
                    retrieval_context is None
                    and item.result is not None
                    and graph_has_retrieval_context(item.result)
                ):
                    retrieval_context = retrieval_context_from_graph_state(item.result, db)
                    retrieval_payload = record_retrieval_completed_event(
                        db, run.id, retrieval_context
                    )
                    yield sse_event(AgentEventType.RETRIEVAL_COMPLETED.value, retrieval_payload)
                if item.kind == "result":
                    result = item.result
                    if result:
                        memory_snapshot = (
                            graph_memory_source_snapshot_json(result) or memory_snapshot
                        )
                    if retrieval_context is not None and (
                        retrieval_context.decision.route == "clarification_required"
                        or retrieval_context.insufficient_evidence
                    ):
                        continue
                if retrieval_context is None:
                    raise RuntimeError("conversation graph streamed an answer before RAG retrieval")
                if not graph_invoked:
                    graph_payload = graph_invoked_payload(
                        route=stream_route,
                        messages=messages,
                        retrieved_chunks=retrieval_context.retrieved_chunks,
                        retrieval_decision=retrieval_context.decision,
                        answer_mode=retrieval_context.answer_mode,
                        selection_context=retrieval_context.knowledge_base_selection,
                        memory_source_snapshot_json=memory_snapshot,
                    )
                    graph_event = append_run_event(
                        db, run.id, AgentEventType.GRAPH_INVOKED, graph_payload
                    )
                    yield sse_event(AgentEventType.GRAPH_INVOKED.value, graph_payload)
                    graph_invoked = True
                if item.kind == "delta":
                    delta_sequence += 1
                    streamed_base_reply_parts.append(item.delta)
                    yield sse_event(
                        "answer_delta",
                        {"delta": item.delta, "sequence": delta_sequence},
                    )
                    continue
        except ResponseProviderConfigurationError as exc:
            yield from _failed_replay_stream_events(
                db,
                run.id,
                conversation_id,
                type(exc).__name__,
                503,
                memory_source_snapshot=memory_snapshot,
            )
            return
        except Exception as exc:
            yield from _failed_replay_stream_events(
                db,
                run.id,
                conversation_id,
                type(exc).__name__,
                502,
                memory_source_snapshot=memory_snapshot,
            )
            return

        if result is None:
            raise RuntimeError("conversation graph stream ended without a final result")
        final_retrieval_context = retrieval_context_from_graph_state(result, db)
        if retrieval_context is None:
            retrieval_payload = record_retrieval_completed_event(
                db, run.id, final_retrieval_context
            )
            yield sse_event(AgentEventType.RETRIEVAL_COMPLETED.value, retrieval_payload)
        retrieval_context = final_retrieval_context
        if retrieval_context.decision.route == "clarification_required":
            route = coerce_route(result.get("route") or classify_messages(messages))
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
                warnings=replay_context.replay_warnings,
                clarification=clarification,
                retrieval_evidence=retrieval_context.retrieval_evidence,
                reasoning_summaries=reasoning_summary_items_from_graph_state(result),
            )
            yield sse_event(
                AgentEventType.ANSWER_COMPOSED.value,
                answer_composed_payload(
                    citation_count=0,
                    reply=response.reply,
                    retrieval_decision=retrieval_context.decision,
                    answer_mode=retrieval_context.answer_mode,
                    selection_context=retrieval_context.knowledge_base_selection,
                    clarification=clarification,
                ),
            )
            _prune_replayed_transcript(db, conversation_id, replay_context, run.id)
            yield sse_event("run_completed", response.model_dump(mode="json"))
            return
        if retrieval_context.insufficient_evidence:
            route = coerce_route(result.get("route") or classify_messages(messages))
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
                warnings=replay_context.replay_warnings,
                insufficient_evidence=True,
                retrieval_evidence=retrieval_context.retrieval_evidence,
                reasoning_summaries=reasoning_summary_items_from_graph_state(result),
            )
            yield sse_event(
                AgentEventType.ANSWER_COMPOSED.value,
                answer_composed_payload(
                    citation_count=0,
                    reply=response.reply,
                    retrieval_decision=retrieval_context.decision,
                    answer_mode=retrieval_context.answer_mode,
                    selection_context=retrieval_context.knowledge_base_selection,
                    insufficient_evidence=True,
                ),
            )
            _prune_replayed_transcript(db, conversation_id, replay_context, run.id)
            yield sse_event("run_completed", response.model_dump(mode="json"))
            return
        log_retrieval_context_for_llm(
            run_id=run.id,
            conversation_id=conversation_id,
            user_id=user_id,
            retrieval_context=retrieval_context,
            graph_input={**graph_input, **result},
        )
        route = coerce_route(result["route"])
        memory_snapshot = graph_memory_source_snapshot_json(result) or memory_snapshot
        update_graph_invoked_event_memory_snapshot(db, graph_event, memory_snapshot)
        base_reply = result.get("reply") or "".join(streamed_base_reply_parts).strip()
        consulted_chunks = chunks_consulted_for_answer(retrieval_context)
        document_coverage = document_coverage_from_graph_state(result)
        reply = compose_rag_reply(base_reply, consulted_chunks, retrieval_context.answer_mode)
        reply, consulted_chunks, completion_insufficient_evidence = _verified_grounding_or_fallback(
            reply=reply,
            consulted_chunks=consulted_chunks,
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
        )
        if not graph_invoked:
            graph_payload = graph_invoked_payload(
                route=route,
                messages=messages,
                retrieved_chunks=retrieval_context.retrieved_chunks,
                retrieval_decision=retrieval_context.decision,
                answer_mode=retrieval_context.answer_mode,
                selection_context=retrieval_context.knowledge_base_selection,
                memory_source_snapshot_json=memory_snapshot,
            )
            graph_event = append_run_event(db, run.id, AgentEventType.GRAPH_INVOKED, graph_payload)
            yield sse_event(AgentEventType.GRAPH_INVOKED.value, graph_payload)
            update_graph_invoked_event_memory_snapshot(db, graph_event, memory_snapshot)
        if not streamed_base_reply_parts:
            for delta in fallback_answer_deltas(reply):
                delta_sequence += 1
                yield sse_event("answer_delta", {"delta": delta, "sequence": delta_sequence})
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
            warnings=replay_context.replay_warnings,
            insufficient_evidence=completion_insufficient_evidence,
            retrieval_evidence=retrieval_context.retrieval_evidence,
            memory_source_snapshot=memory_snapshot,
            document_coverage=document_coverage,
            retrieval_latency_ms=retrieval_context.retrieval_latency_ms,
            reasoning_summaries=reasoning_summary_items_from_graph_state(result),
        )
        if document_coverage is not None:
            yield sse_event(
                AgentEventType.FULL_DOCUMENT_READ.value,
                {
                    **document_coverage.model_dump(mode="json"),
                    "latency_ms": retrieval_context.retrieval_latency_ms,
                },
            )
        yield sse_event(
            AgentEventType.ANSWER_COMPOSED.value,
            answer_composed_payload(
                citation_count=len(response.citations),
                reply=reply,
                retrieval_decision=retrieval_context.decision,
                answer_mode=retrieval_context.answer_mode,
                selection_context=retrieval_context.knowledge_base_selection,
                insufficient_evidence=completion_insufficient_evidence,
            ),
        )
        delete_checkpoint_thread(graph_runner, run.id)
        _prune_replayed_transcript(db, conversation_id, replay_context, run.id)
        yield sse_event("run_completed", response.model_dump(mode="json"))
    except GeneratorExit:
        if is_run_active(db, run.id):
            fail_active_run(
                db=db,
                run_id=run.id,
                conversation_id=conversation_id,
                error_type="GeneratorExit",
            )
        delete_checkpoint_thread(graph_runner, run.id)
        raise
    except ResponseProviderConfigurationError as exc:
        delete_checkpoint_thread(graph_runner, run.id)
        yield from _failed_replay_stream_events(
            db, run.id, conversation_id, type(exc).__name__, 503
        )
    except Exception as exc:
        delete_checkpoint_thread(graph_runner, run.id)
        yield from _failed_replay_stream_events(
            db, run.id, conversation_id, type(exc).__name__, 502
        )


def _failed_replay_stream_events(
    db: Session,
    run_id: str,
    conversation_id: str,
    error_type: str,
    status_code: int,
    *,
    memory_source_snapshot: str | None = None,
) -> Iterator[str]:
    persisted_run_id = persist_failed_run(
        db=db,
        run_id=run_id,
        conversation_id=conversation_id,
        error_type=error_type,
        memory_source_snapshot=memory_source_snapshot,
    )
    yield sse_event(
        AgentEventType.RUN_FAILED.value,
        {"run_id": persisted_run_id, "safe_error_type": error_type},
    )
    yield sse_event("run_error", {"run_id": persisted_run_id, "status_code": status_code})


def _prune_replayed_transcript(
    db: Session,
    conversation_id: str,
    replay_context: ReplayContext,
    preserved_run_id: str,
) -> None:
    prune_conversation_from_message(
        db,
        conversation_id=conversation_id,
        target_message=replay_context.target_message,
        removed_messages=replay_context.removed_messages,
        original_run=replay_context.original_run,
        preserved_run_ids={preserved_run_id},
    )


def source_warnings_for_replay(
    db: Session, original_run: AgentRunModel | None
) -> list[ConversationRunWarning]:
    if original_run is None or original_run.retrieval_source_snapshot_json is None:
        return []
    sources = _retrieval_source_snapshot(original_run.retrieval_source_snapshot_json)
    if not sources:
        return []
    document_ids = [source["document_id"] for source in sources if source.get("document_id")]
    if not document_ids:
        return []
    existing_ids = set(
        db.scalars(select(DocumentModel.id).where(DocumentModel.id.in_(document_ids))).all()
    )
    missing_sources = [
        source for source in sources if source.get("document_id") not in existing_ids
    ]
    if not missing_sources:
        return []
    missing_document_ids = sorted(
        {source["document_id"] for source in missing_sources if source.get("document_id")}
    )
    missing_source_filenames = sorted(
        {source["source_filename"] for source in missing_sources if source.get("source_filename")}
    )
    return [
        ConversationRunWarning(
            code="regeneration_sources_unavailable",
            message=(
                "Some sources used in the original answer are no longer available. "
                "This regeneration used currently available knowledge only."
            ),
            missing_document_ids=missing_document_ids,
            missing_source_filenames=missing_source_filenames,
        )
    ]


def _full_document_source_for_run(
    db: Session,
    original_run: AgentRunModel | None,
) -> dict[str, str | None] | None:
    if original_run is None:
        return None
    event = db.scalar(
        select(AgentEventModel)
        .where(
            AgentEventModel.run_id == original_run.id,
            AgentEventModel.event_type == AgentEventType.FULL_DOCUMENT_READ.value,
        )
        .order_by(AgentEventModel.sequence.desc())
        .limit(1)
    )
    if event is None:
        return None
    try:
        payload = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("document_id"), str):
        return None
    source_filename = payload.get("source_filename")
    return {
        "document_id": payload["document_id"],
        "source_filename": source_filename if isinstance(source_filename, str) else None,
    }


def _with_unavailable_full_document_warning(
    warnings: list[ConversationRunWarning],
    *,
    document_id: str,
    source_filename: str | None,
) -> list[ConversationRunWarning]:
    existing_document_ids = {
        missing_id for warning in warnings for missing_id in warning.missing_document_ids
    }
    if document_id in existing_document_ids:
        return warnings
    return [
        *warnings,
        ConversationRunWarning(
            code="regeneration_sources_unavailable",
            message=(
                "The full document used in the original answer is no longer authorized or "
                "available. This regeneration did not substitute a different document."
            ),
            missing_document_ids=[document_id],
            missing_source_filenames=[source_filename] if source_filename else [],
        ),
    ]


def _retrieval_source_snapshot(raw_json: str) -> list[dict[str, str | None]]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    sources: list[dict[str, str | None]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        document_id = item.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            continue
        source_filename = item.get("source_filename")
        sources.append(
            {
                "document_id": document_id,
                "source_filename": source_filename if isinstance(source_filename, str) else None,
            }
        )
    return sources


def _json_string_list(raw_json: str | None) -> list[str]:
    try:
        parsed = json.loads(raw_json or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []
