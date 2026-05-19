"""Conversation and product chat-run API routes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from time import perf_counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from my_agents.agents.general_assistant.classifier import classify_messages
from my_agents.agents.general_assistant.responders import ResponseProviderConfigurationError
from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.conversations.models import (
    AgentEventModel,
    AgentEventType,
    AgentRunModel,
    ConversationModel,
    MessageModel,
    MessageRole,
    RunStatus,
)
from my_agents.conversations.schemas import (
    AgentEventResponse,
    AgentRunSummaryResponse,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationRunRequest,
    ConversationRunResponse,
    MessageCreateRequest,
    MessageResponse,
)
from my_agents.groups.models import MembershipModel
from my_agents.knowledge.models import CitationModel
from my_agents.knowledge.retrieval import RetrievalService, RetrievedChunk
from my_agents.knowledge.schemas import CitationResponse
from my_agents.persistence.database import get_database_session
from my_agents.schemas import RouteDecision

conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])

GraphStreamItemKind = Literal["delta", "result"]


@dataclass(frozen=True)
class GraphStreamItem:
    """Internal item emitted while adapting graph stream events to SSE."""

    kind: GraphStreamItemKind
    delta: str = ""
    result: dict[str, Any] | None = None


@conversations_router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    request: ConversationCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ConversationResponse:
    if request.group_id is not None:
        _require_group_membership(db, request.group_id, principal.user_id)
    conversation = ConversationModel(
        owner_user_id=principal.user_id,
        group_id=request.group_id,
        title=request.title.strip(),
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_response(conversation)


@conversations_router.get("", response_model=list[ConversationResponse])
def list_conversations(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[ConversationResponse]:
    group_ids = select(MembershipModel.group_id).where(MembershipModel.user_id == principal.user_id)
    conversations = db.scalars(
        select(ConversationModel).where(
            or_(
                ConversationModel.owner_user_id == principal.user_id,
                ConversationModel.group_id.in_(group_ids),
            )
        )
    ).all()
    return [_conversation_response(conversation) for conversation in conversations]


@conversations_router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ConversationResponse:
    conversation = _get_authorized_conversation(db, conversation_id, principal.user_id)
    return _conversation_response(conversation)


@conversations_router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_message(
    conversation_id: str,
    request: MessageCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> MessageResponse:
    _get_authorized_conversation(db, conversation_id, principal.user_id)
    message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.USER.value,
        content=request.content.strip(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return _message_response(message)


@conversations_router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[MessageResponse]:
    """Return the authorized server-owned transcript for a conversation."""
    _get_authorized_conversation(db, conversation_id, principal.user_id)
    messages = db.scalars(
        select(MessageModel)
        .where(MessageModel.conversation_id == conversation_id)
        .order_by(MessageModel.created_at, MessageModel.id)
    ).all()
    return [_message_response(message) for message in messages]


@conversations_router.post("/{conversation_id}/runs", response_model=ConversationRunResponse)
def run_conversation(
    conversation_id: str,
    request: ConversationRunRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
) -> ConversationRunResponse:
    _get_authorized_conversation(db, conversation_id, principal.user_id)
    user_message = _store_user_message(db, conversation_id, request.message)

    messages = _messages_for_conversation(db, conversation_id)
    retrieved_chunks, retrieval_latency_ms = _retrieve_authorized_context(
        db=db,
        user_id=principal.user_id,
        message=request.message,
    )
    graph_input = {
        "messages": messages,
        "principal_id": principal.user_id,
        "conversation_id": conversation_id,
        "retrieved_chunk_ids": [item.chunk.id for item in retrieved_chunks],
    }
    try:
        result = graph_runner.invoke(graph_input)
    except ResponseProviderConfigurationError as exc:
        _persist_failed_run(
            db=db,
            conversation_id=conversation_id,
            user_id=principal.user_id,
            user_message_id=user_message.id,
            message_content_length=len(request.message.strip()),
            retrieved_chunks=retrieved_chunks,
            retrieval_latency_ms=retrieval_latency_ms,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="conversation run failed") from exc
    except Exception as exc:
        _persist_failed_run(
            db=db,
            conversation_id=conversation_id,
            user_id=principal.user_id,
            user_message_id=user_message.id,
            message_content_length=len(request.message.strip()),
            retrieved_chunks=retrieved_chunks,
            retrieval_latency_ms=retrieval_latency_ms,
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="conversation run failed") from exc
    route = _coerce_route(result["route"])
    reply = _compose_rag_reply(result["reply"], retrieved_chunks)
    return _persist_completed_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        user_message_id=user_message.id,
        message_content_length=len(request.message.strip()),
        messages=messages,
        retrieved_chunks=retrieved_chunks,
        retrieval_latency_ms=retrieval_latency_ms,
        route=route,
        reply=reply,
    )


@conversations_router.post(
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
) -> StreamingResponse:
    """Stream redacted conversation-run progress as Server-Sent Events.

    The stream keeps progress events compatible, emits incremental `answer_delta` text
    while the graph/provider streams, then finishes with `run_completed` containing the
    same response shape returned by the non-streaming `/runs` endpoint. If graph execution
    fails after streaming starts, the endpoint persists the failed run and emits
    `run_failed` plus `run_error` events instead of leaking raw prompts or provider
    exception text.
    """
    _get_authorized_conversation(db, conversation_id, principal.user_id)
    return StreamingResponse(
        _conversation_run_events(
            db=db,
            conversation_id=conversation_id,
            request=request,
            user_id=principal.user_id,
            graph_runner=graph_runner,
        ),
        media_type="text/event-stream",
    )


def _conversation_run_events(
    *,
    db: Session,
    conversation_id: str,
    request: ConversationRunRequest,
    user_id: str,
    graph_runner: GraphRunner,
) -> Iterator[str]:
    user_message = _store_user_message(db, conversation_id, request.message)
    message_content_length = len(request.message.strip())
    user_message_payload = _user_message_stored_payload(
        message_id=user_message.id,
        content_length=message_content_length,
    )
    yield _sse_event(AgentEventType.USER_MESSAGE_STORED.value, user_message_payload)

    messages = _messages_for_conversation(db, conversation_id)
    retrieved_chunks, retrieval_latency_ms = _retrieve_authorized_context(
        db=db,
        user_id=user_id,
        message=request.message,
    )
    retrieval_payload = _retrieval_completed_payload(
        retrieved_chunks=retrieved_chunks,
        retrieval_latency_ms=retrieval_latency_ms,
    )
    yield _sse_event(AgentEventType.RETRIEVAL_COMPLETED.value, retrieval_payload)

    graph_input = {
        "messages": messages,
        "principal_id": user_id,
        "conversation_id": conversation_id,
        "retrieved_chunk_ids": [item.chunk.id for item in retrieved_chunks],
    }
    stream_route = classify_messages(messages)
    graph_invoked = False
    delta_sequence = 0
    streamed_base_reply_parts: list[str] = []
    result: dict[str, Any] | None = None
    try:
        for item in _stream_graph_items(graph_runner=graph_runner, graph_input=graph_input):
            if not graph_invoked:
                yield _sse_event(
                    AgentEventType.GRAPH_INVOKED.value,
                    _graph_invoked_payload(
                        route=stream_route,
                        messages=messages,
                        retrieved_chunks=retrieved_chunks,
                    ),
                )
                graph_invoked = True
            if item.kind == "delta":
                delta_sequence += 1
                streamed_base_reply_parts.append(item.delta)
                yield _sse_event(
                    "answer_delta",
                    {"delta": item.delta, "sequence": delta_sequence},
                )
                continue
            result = item.result
    except ResponseProviderConfigurationError as exc:
        run_id = _persist_failed_run(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            user_message_id=user_message.id,
            message_content_length=message_content_length,
            retrieved_chunks=retrieved_chunks,
            retrieval_latency_ms=retrieval_latency_ms,
            error_type=type(exc).__name__,
        )
        yield _sse_event(
            AgentEventType.RUN_FAILED.value,
            {"run_id": run_id, "safe_error_type": type(exc).__name__},
        )
        yield _sse_event("run_error", {"run_id": run_id, "status_code": 503})
        return
    except Exception as exc:
        run_id = _persist_failed_run(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            user_message_id=user_message.id,
            message_content_length=message_content_length,
            retrieved_chunks=retrieved_chunks,
            retrieval_latency_ms=retrieval_latency_ms,
            error_type=type(exc).__name__,
        )
        yield _sse_event(
            AgentEventType.RUN_FAILED.value,
            {"run_id": run_id, "safe_error_type": type(exc).__name__},
        )
        yield _sse_event("run_error", {"run_id": run_id, "status_code": 502})
        return

    if result is None:
        raise RuntimeError("conversation graph stream ended without a final result")
    route = _coerce_route(result["route"])
    base_reply = result.get("reply") or "".join(streamed_base_reply_parts).strip()
    reply = _compose_rag_reply(base_reply, retrieved_chunks)
    if not graph_invoked:
        yield _sse_event(
            AgentEventType.GRAPH_INVOKED.value,
            _graph_invoked_payload(
                route=route,
                messages=messages,
                retrieved_chunks=retrieved_chunks,
            ),
        )
    if not streamed_base_reply_parts:
        for delta in _fallback_answer_deltas(base_reply):
            delta_sequence += 1
            yield _sse_event("answer_delta", {"delta": delta, "sequence": delta_sequence})
    response = _persist_completed_run(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
        user_message_id=user_message.id,
        message_content_length=message_content_length,
        messages=messages,
        retrieved_chunks=retrieved_chunks,
        retrieval_latency_ms=retrieval_latency_ms,
        route=route,
        reply=reply,
    )
    yield _sse_event(
        AgentEventType.ANSWER_COMPOSED.value,
        _answer_composed_payload(citation_count=len(response.citations), reply=reply),
    )
    yield _sse_event("run_completed", response.model_dump(mode="json"))


def _persist_completed_run(
    *,
    db: Session,
    conversation_id: str,
    user_id: str,
    user_message_id: str,
    message_content_length: int,
    messages: list[BaseMessage],
    retrieved_chunks: list[RetrievedChunk],
    retrieval_latency_ms: float,
    route: RouteDecision,
    reply: str,
) -> ConversationRunResponse:
    assistant_message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT.value,
        content=reply,
    )
    run = AgentRunModel(
        conversation_id=conversation_id,
        user_id=user_id,
        status=RunStatus.COMPLETED.value,
        route_label=route.label,
    )
    db.add_all([assistant_message, run])
    db.flush()
    citations = [
        CitationModel(
            run_id=run.id,
            document_id=item.document.id,
            chunk_id=item.chunk.id,
            snippet=item.chunk.content[:240],
        )
        for item in retrieved_chunks
    ]
    events = [
        _event(
            run.id,
            1,
            AgentEventType.USER_MESSAGE_STORED,
            _user_message_stored_payload(
                message_id=user_message_id,
                content_length=message_content_length,
            ),
        ),
        _event(
            run.id,
            2,
            AgentEventType.RETRIEVAL_COMPLETED,
            _retrieval_completed_payload(
                retrieved_chunks=retrieved_chunks,
                retrieval_latency_ms=retrieval_latency_ms,
            ),
        ),
        _event(
            run.id,
            3,
            AgentEventType.GRAPH_INVOKED,
            _graph_invoked_payload(
                route=route,
                messages=messages,
                retrieved_chunks=retrieved_chunks,
            ),
        ),
        _event(
            run.id,
            4,
            AgentEventType.ANSWER_COMPOSED,
            _answer_composed_payload(citation_count=len(citations), reply=reply),
        ),
    ]
    db.add_all([*citations, *events])
    db.commit()
    db.refresh(run)
    for citation in citations:
        db.refresh(citation)
    return ConversationRunResponse(
        run_id=run.id,
        conversation_id=conversation_id,
        reply=reply,
        route=route,
        handled_by="personal_assistant_graph",
        citations=[
            CitationResponse(
                id=citation.id,
                document_id=citation.document_id,
                chunk_id=citation.chunk_id,
                snippet=citation.snippet,
            )
            for citation in citations
        ],
    )


@conversations_router.get(
    "/{conversation_id}/runs",
    response_model=list[AgentRunSummaryResponse],
)
def list_runs(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[AgentRunSummaryResponse]:
    """Return frontend-safe run history for an authorized conversation."""
    _get_authorized_conversation(db, conversation_id, principal.user_id)
    runs = db.scalars(
        select(AgentRunModel)
        .where(AgentRunModel.conversation_id == conversation_id)
        .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
    ).all()
    return [_run_summary_response(run) for run in runs]


@conversations_router.get(
    "/{conversation_id}/runs/{run_id}/events",
    response_model=list[AgentEventResponse],
)
def list_run_events(
    conversation_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[AgentEventResponse]:
    _get_authorized_conversation(db, conversation_id, principal.user_id)
    run = db.get(AgentRunModel, run_id)
    if run is None or run.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    events = db.scalars(
        select(AgentEventModel)
        .where(AgentEventModel.run_id == run_id)
        .order_by(AgentEventModel.sequence)
    ).all()
    return [_event_response(event) for event in events]


def _get_authorized_conversation(
    db: Session, conversation_id: str, user_id: str
) -> ConversationModel:
    conversation = db.get(ConversationModel, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    if conversation.owner_user_id == user_id:
        return conversation
    if conversation.group_id is not None and _has_group_membership(
        db, conversation.group_id, user_id
    ):
        return conversation
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")


def _require_group_membership(db: Session, group_id: str, user_id: str) -> None:
    if not _has_group_membership(db, group_id, user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")


def _has_group_membership(db: Session, group_id: str, user_id: str) -> bool:
    return (
        db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == group_id,
                MembershipModel.user_id == user_id,
            )
        )
        is not None
    )


def _messages_for_conversation(db: Session, conversation_id: str) -> list[BaseMessage]:
    persisted = db.scalars(
        select(MessageModel)
        .where(MessageModel.conversation_id == conversation_id)
        .order_by(MessageModel.created_at, MessageModel.id)
    ).all()
    messages: list[BaseMessage] = []
    for message in persisted:
        if message.role == MessageRole.ASSISTANT.value:
            messages.append(AIMessage(content=message.content))
        else:
            messages.append(HumanMessage(content=message.content))
    return messages


def _store_user_message(db: Session, conversation_id: str, message: str) -> MessageModel:
    user_message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.USER.value,
        content=message.strip(),
    )
    db.add(user_message)
    db.commit()
    db.refresh(user_message)
    return user_message


def _retrieve_authorized_context(
    *,
    db: Session,
    user_id: str,
    message: str,
) -> tuple[list[RetrievedChunk], float]:
    retrieval_started = perf_counter()
    retrieved_chunks = RetrievalService(db).retrieve(
        user_id=user_id,
        query=message,
    )
    retrieval_latency_ms = round((perf_counter() - retrieval_started) * 1000, 3)
    return retrieved_chunks, retrieval_latency_ms


def _stream_graph_items(
    *,
    graph_runner: GraphRunner,
    graph_input: dict,
) -> Iterator[GraphStreamItem]:
    """Yield assistant text deltas plus one final graph result.

    The compiled LangGraph runner supports `.stream(...)` in local CLI usage. Tests and
    simple spies may only implement `.invoke(...)`, so this adapter falls back to invoke
    while still emitting deterministic `answer_delta` chunks before `run_completed`.
    """
    stream = getattr(graph_runner, "stream", None)
    if not callable(stream):
        yield GraphStreamItem(kind="result", result=graph_runner.invoke(graph_input))
        return

    streamed_parts: list[str] = []
    final_result: dict[str, Any] = {}
    emitted_stream_event = False
    for event in stream(
        graph_input,
        stream_mode=["messages", "updates"],
        version="v2",
    ):
        emitted_stream_event = True
        event_type, event_data = _stream_event_parts(event)
        if event_type == "messages":
            message_chunk, _metadata = event_data
            text = _message_chunk_text(message_chunk)
            if text:
                streamed_parts.append(text)
                yield GraphStreamItem(kind="delta", delta=text)
            continue
        if event_type == "updates" and isinstance(event_data, dict):
            final_result.update(_result_fields_from_update(event_data))

    if "reply" not in final_result and streamed_parts:
        final_result["reply"] = "".join(streamed_parts).strip()
    if "route" not in final_result:
        final_result["route"] = classify_messages(graph_input.get("messages", []))
    if "reply" in final_result:
        yield GraphStreamItem(kind="result", result=final_result)
        return
    if not emitted_stream_event:
        yield GraphStreamItem(kind="result", result=graph_runner.invoke(graph_input))
        return
    raise RuntimeError("graph stream did not yield a reply")


def _stream_event_parts(event: Any) -> tuple[str | None, Any]:
    if isinstance(event, dict):
        return event.get("type"), event.get("data")
    if isinstance(event, tuple) and len(event) == 2:
        return event
    return None, None


def _result_fields_from_update(update: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for node_update in update.values():
        if not isinstance(node_update, dict):
            continue
        route = node_update.get("route")
        if route is not None:
            fields["route"] = route
        reply = node_update.get("reply")
        if isinstance(reply, str):
            fields["reply"] = reply
    return fields


def _message_chunk_text(message_chunk: Any) -> str:
    text = getattr(message_chunk, "text", "")
    if isinstance(text, str) and text:
        return str(text)

    content = getattr(message_chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "".join(parts)
    return ""


def _fallback_answer_deltas(reply: str) -> list[str]:
    words = reply.split(" ")
    if len(words) <= 1:
        return [reply] if reply else []
    return [f"{word} " for word in words[:-1]] + [words[-1]]


def _coerce_route(route: RouteDecision | dict) -> RouteDecision:
    if isinstance(route, RouteDecision):
        return route
    return RouteDecision.model_validate(route)


def _compose_rag_reply(base_reply: str, retrieved_chunks: list[RetrievedChunk]) -> str:
    if not retrieved_chunks:
        return base_reply
    context_lines = [
        f"- {item.document.title}: {item.chunk.content[:180]}" for item in retrieved_chunks[:3]
    ]
    return (
        "Based on authorized knowledge context:\n" + "\n".join(context_lines) + "\n\n" + base_reply
    )


def _count_retrieval_source(retrieved_chunks: list[RetrievedChunk], source: str) -> int:
    return sum(1 for chunk in retrieved_chunks if chunk.source == source)


def _user_message_stored_payload(*, message_id: str, content_length: int) -> dict:
    return {"message_id": message_id, "content_length": content_length}


def _retrieval_completed_payload(
    *,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_latency_ms: float,
) -> dict:
    return {
        "authorized_context_count": len(retrieved_chunks),
        "direct_count": _count_retrieval_source(retrieved_chunks, "vector_fixture"),
        "graph_expansion_count": _count_retrieval_source(retrieved_chunks, "graph_expansion"),
        "latency_ms": retrieval_latency_ms,
    }


def _graph_invoked_payload(
    *,
    route: RouteDecision,
    messages: list[BaseMessage],
    retrieved_chunks: list[RetrievedChunk],
) -> dict:
    return {
        "route_label": route.label,
        "message_count": len(messages),
        "retrieved_chunk_count": len(retrieved_chunks),
    }


def _answer_composed_payload(*, citation_count: int, reply: str) -> dict:
    return {"citation_count": citation_count, "reply_length": len(reply)}


def _persist_failed_run(
    *,
    db: Session,
    conversation_id: str,
    user_id: str,
    user_message_id: str,
    message_content_length: int,
    retrieved_chunks: list[RetrievedChunk],
    retrieval_latency_ms: float,
    error_type: str,
) -> str:
    run = AgentRunModel(
        conversation_id=conversation_id,
        user_id=user_id,
        status=RunStatus.FAILED.value,
        route_label=None,
    )
    db.add(run)
    db.flush()
    db.add_all(
        [
            _event(
                run.id,
                1,
                AgentEventType.USER_MESSAGE_STORED,
                _user_message_stored_payload(
                    message_id=user_message_id,
                    content_length=message_content_length,
                ),
            ),
            _event(
                run.id,
                2,
                AgentEventType.RETRIEVAL_COMPLETED,
                _retrieval_completed_payload(
                    retrieved_chunks=retrieved_chunks,
                    retrieval_latency_ms=retrieval_latency_ms,
                ),
            ),
            _event(
                run.id,
                3,
                AgentEventType.RUN_FAILED,
                {"safe_error_type": error_type},
            ),
        ]
    )
    db.commit()
    return run.id


def _event(
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


def _event_response(event: AgentEventModel) -> AgentEventResponse:
    return AgentEventResponse(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        payload=json.loads(event.payload_json),
    )


def _sse_event(event_name: str, payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"


def _conversation_response(conversation: ConversationModel) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        owner_user_id=conversation.owner_user_id,
        group_id=conversation.group_id,
    )


def _run_summary_response(run: AgentRunModel) -> AgentRunSummaryResponse:
    return AgentRunSummaryResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        route_label=run.route_label,
        created_at=run.created_at,
    )


def _message_response(message: MessageModel) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
    )
