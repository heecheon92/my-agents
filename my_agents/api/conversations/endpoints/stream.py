"""Streaming conversation run endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.responders import ResponseProviderConfigurationError
from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.graph_streaming import fallback_answer_deltas, stream_graph_items
from my_agents.api.conversations.retrieval_context import (
    chunks_used_for_answer,
    clarification_request,
    compose_rag_reply,
    graph_input_for_run,
    insufficient_evidence_reply,
    log_retrieval_context_for_llm,
    memory_source_snapshot_json,
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
from my_agents.api.conversations.run_lifecycle import (
    _verified_grounding_or_fallback,
    assert_no_active_run,
    cancelled_sse_event,
    fail_active_run,
    is_run_active,
    is_run_cancelling,
    persist_completed_run,
    persist_failed_run,
    record_run_retrieval_metadata,
    start_run,
)
from my_agents.api.conversations.serializers import (
    coerce_route,
    knowledge_base_selection_payload,
)
from my_agents.api.conversations.transcripts import messages_for_conversation, store_user_message
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_can_send_prompt
from my_agents.conversations.models import AgentEventType
from my_agents.conversations.schemas import ConversationRunRequest
from my_agents.knowledge.auth import (
    KnowledgeBaseSelectionContext,
    resolve_conversation_knowledge_context,
)
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

router = APIRouter()


@router.post(
    "/{conversation_id}/runs/stream",
    responses={
        200: {
            "description": (
                "Server-Sent Events stream with progress, answer_delta, and run_completed events."
            ),
            "content": {
                "text/event-stream": {
                    "example": (
                        "event: answer_delta\n"
                        'data: {"delta":"Hello","sequence":1}\n\n'
                        "event: run_completed\n"
                        'data: {"run_id":"...","reply":"Hello"}\n\n'
                    )
                }
            },
        }
    },
)
def stream_conversation_run(
    conversation_id: str,
    request: ConversationRunRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Stream redacted conversation-run progress as Server-Sent Events.

    The stream keeps progress events compatible, emits incremental `answer_delta` text
    while the graph/provider streams, then finishes with `run_completed` containing the
    same response shape returned by the non-streaming `/runs` endpoint. If graph execution
    fails after streaming starts, the endpoint persists the failed run and emits
    `run_failed` plus `run_error` events instead of leaking raw prompts or provider
    exception text.
    """
    assert_guest_can_send_prompt(db, principal, settings)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    assert_no_active_run(db, conversation_id)
    selection_context = resolve_conversation_knowledge_context(
        db,
        user_id=principal.user_id,
        requested_selection=request.knowledge_base_selection,
    )
    return StreamingResponse(
        conversation_run_events(
            db=db,
            conversation_id=conversation_id,
            request=request,
            user_id=principal.user_id,
            selection_context=selection_context,
            graph_runner=graph_runner,
        ),
        media_type="text/event-stream",
    )


def conversation_run_events(
    *,
    db: Session,
    conversation_id: str,
    request: ConversationRunRequest,
    user_id: str,
    selection_context: KnowledgeBaseSelectionContext,
    graph_runner: GraphRunner,
) -> Iterator[str]:
    user_message = store_user_message(db, conversation_id, request.message)
    message_content_length = len(request.message.strip())
    run = start_run(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
        user_message_id=user_message.id,
        message_content_length=message_content_length,
        selection_context=selection_context,
    )
    try:
        yield sse_event(
            AgentEventType.RUN_STARTED.value,
            {
                "run_id": run.id,
                "conversation_id": conversation_id,
                "status": run.status,
                **knowledge_base_selection_payload(selection_context),
            },
        )
        user_message_payload = user_message_stored_payload(
            message_id=user_message.id,
            content_length=message_content_length,
        )
        yield sse_event(AgentEventType.USER_MESSAGE_STORED.value, user_message_payload)

        messages = messages_for_conversation(db, conversation_id)
        retrieval_context = prepare_retrieval_context(
            db=db,
            user_id=user_id,
            conversation_id=conversation_id,
            message=request.message,
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
        retrieval_payload = retrieval_completed_payload(
            retrieved_chunks=retrieval_context.retrieved_chunks,
            retrieval_latency_ms=retrieval_context.retrieval_latency_ms,
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            retrieval_evidence=retrieval_context.retrieval_evidence,
            retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
            insufficient_evidence=retrieval_context.insufficient_evidence,
        )
        append_run_event(db, run.id, AgentEventType.RETRIEVAL_COMPLETED, retrieval_payload)
        yield sse_event(AgentEventType.RETRIEVAL_COMPLETED.value, retrieval_payload)
        if is_run_cancelling(db, run.id):
            yield cancelled_sse_event(db, run.id)
            return

        if retrieval_context.decision.route == "clarification_required":
            route = classify_messages(messages)
            clarification = clarification_request(retrieval_context.decision)
            response = persist_completed_run(
                db=db,
                run_id=run.id,
                conversation_id=conversation_id,
                retrieved_chunks=[],
                route=route,
                reply="",
                retrieval_decision=retrieval_context.decision,
                answer_mode=retrieval_context.answer_mode,
                selection_context=retrieval_context.knowledge_base_selection,
                clarification=clarification,
                retrieval_evidence=retrieval_context.retrieval_evidence,
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
            yield sse_event("run_completed", response.model_dump(mode="json"))
            return

        if retrieval_context.insufficient_evidence:
            route = classify_messages(messages)
            response = persist_completed_run(
                db=db,
                run_id=run.id,
                conversation_id=conversation_id,
                retrieved_chunks=[],
                route=route,
                reply=insufficient_evidence_reply(),
                retrieval_decision=retrieval_context.decision,
                answer_mode=retrieval_context.answer_mode,
                selection_context=retrieval_context.knowledge_base_selection,
                insufficient_evidence=True,
                retrieval_evidence=retrieval_context.retrieval_evidence,
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
            yield sse_event("run_completed", response.model_dump(mode="json"))
            return

        graph_input = graph_input_for_run(
            db=db,
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
        memory_snapshot = memory_source_snapshot_json(
            memory_context=graph_input["memory_context"],  # type: ignore[arg-type]
            source_conflicts=graph_input["source_conflicts"],  # type: ignore[arg-type]
        )
        stream_route = classify_messages(messages)
        graph_invoked = False
        delta_sequence = 0
        streamed_base_reply_parts: list[str] = []
        result: dict[str, Any] | None = None
        try:
            for item in stream_graph_items(graph_runner=graph_runner, graph_input=graph_input):
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
                    append_run_event(db, run.id, AgentEventType.GRAPH_INVOKED, graph_payload)
                    yield sse_event(
                        AgentEventType.GRAPH_INVOKED.value,
                        graph_payload,
                    )
                    graph_invoked = True
                if is_run_cancelling(db, run.id):
                    yield cancelled_sse_event(db, run.id)
                    return
                if item.kind == "delta":
                    delta_sequence += 1
                    streamed_base_reply_parts.append(item.delta)
                    yield sse_event(
                        "answer_delta",
                        {"delta": item.delta, "sequence": delta_sequence},
                    )
                    continue
                result = item.result
        except ResponseProviderConfigurationError as exc:
            run_id = persist_failed_run(
                db=db,
                run_id=run.id,
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )
            yield sse_event(
                AgentEventType.RUN_FAILED.value,
                {"run_id": run_id, "safe_error_type": type(exc).__name__},
            )
            yield sse_event("run_error", {"run_id": run_id, "status_code": 503})
            return
        except Exception as exc:
            run_id = persist_failed_run(
                db=db,
                run_id=run.id,
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )
            yield sse_event(
                AgentEventType.RUN_FAILED.value,
                {"run_id": run_id, "safe_error_type": type(exc).__name__},
            )
            yield sse_event("run_error", {"run_id": run_id, "status_code": 502})
            return

        if result is None:
            raise RuntimeError("conversation graph stream ended without a final result")
        route = coerce_route(result["route"])
        base_reply = result.get("reply") or "".join(streamed_base_reply_parts).strip()
        used_chunks = chunks_used_for_answer(retrieval_context)
        reply = compose_rag_reply(base_reply, used_chunks, retrieval_context.answer_mode)
        reply, used_chunks, completion_insufficient_evidence = _verified_grounding_or_fallback(
            reply=reply,
            cited_chunks=used_chunks,
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
            append_run_event(db, run.id, AgentEventType.GRAPH_INVOKED, graph_payload)
            yield sse_event(
                AgentEventType.GRAPH_INVOKED.value,
                graph_payload,
            )
        if not streamed_base_reply_parts:
            for delta in fallback_answer_deltas(reply):
                if is_run_cancelling(db, run.id):
                    yield cancelled_sse_event(db, run.id)
                    return
                delta_sequence += 1
                yield sse_event("answer_delta", {"delta": delta, "sequence": delta_sequence})
        if is_run_cancelling(db, run.id):
            yield cancelled_sse_event(db, run.id)
            return
        response = persist_completed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            retrieved_chunks=used_chunks,
            route=route,
            reply=reply,
            retrieval_decision=retrieval_context.decision,
            answer_mode=retrieval_context.answer_mode,
            selection_context=retrieval_context.knowledge_base_selection,
            insufficient_evidence=completion_insufficient_evidence,
            retrieval_evidence=retrieval_context.retrieval_evidence,
            memory_source_snapshot=memory_snapshot,
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
        yield sse_event("run_completed", response.model_dump(mode="json"))
    except GeneratorExit:
        if is_run_active(db, run.id):
            cancelled_sse_event(db, run.id)
        raise
    except ResponseProviderConfigurationError as exc:
        run_id = fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        if run_id is not None:
            yield sse_event(
                AgentEventType.RUN_FAILED.value,
                {"run_id": run_id, "safe_error_type": type(exc).__name__},
            )
            yield sse_event("run_error", {"run_id": run_id, "status_code": 503})
        return
    except Exception as exc:
        run_id = fail_active_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        if run_id is not None:
            yield sse_event(
                AgentEventType.RUN_FAILED.value,
                {"run_id": run_id, "safe_error_type": type(exc).__name__},
            )
            yield sse_event("run_error", {"run_id": run_id, "status_code": 502})
        return
