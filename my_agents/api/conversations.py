"""Conversation and product chat-run API routes."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

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


@conversations_router.post("/{conversation_id}/runs", response_model=ConversationRunResponse)
def run_conversation(
    conversation_id: str,
    request: ConversationRunRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
) -> ConversationRunResponse:
    _get_authorized_conversation(db, conversation_id, principal.user_id)
    user_message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.USER.value,
        content=request.message.strip(),
    )
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    messages = _messages_for_conversation(db, conversation_id)
    retrieval_started = perf_counter()
    retrieved_chunks = RetrievalService(db).retrieve(
        user_id=principal.user_id,
        query=request.message,
    )
    retrieval_latency_ms = round((perf_counter() - retrieval_started) * 1000, 3)
    result = graph_runner.invoke(
        {
            "messages": messages,
            "principal_id": principal.user_id,
            "conversation_id": conversation_id,
            "retrieved_chunk_ids": [item.chunk.id for item in retrieved_chunks],
        }
    )
    route = _coerce_route(result["route"])
    reply = _compose_rag_reply(result["reply"], retrieved_chunks)
    assistant_message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT.value,
        content=reply,
    )
    run = AgentRunModel(
        conversation_id=conversation_id,
        user_id=principal.user_id,
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
            {"message_id": user_message.id, "content_length": len(request.message.strip())},
        ),
        _event(
            run.id,
            2,
            AgentEventType.RETRIEVAL_COMPLETED,
            {
                "authorized_context_count": len(retrieved_chunks),
                "direct_count": _count_retrieval_source(retrieved_chunks, "vector_fixture"),
                "graph_expansion_count": _count_retrieval_source(
                    retrieved_chunks, "graph_expansion"
                ),
                "latency_ms": retrieval_latency_ms,
            },
        ),
        _event(
            run.id,
            3,
            AgentEventType.GRAPH_INVOKED,
            {
                "route_label": route.label,
                "message_count": len(messages),
                "retrieved_chunk_count": len(retrieved_chunks),
            },
        ),
        _event(
            run.id,
            4,
            AgentEventType.ANSWER_COMPOSED,
            {"citation_count": len(citations), "reply_length": len(reply)},
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


def _conversation_response(conversation: ConversationModel) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        owner_user_id=conversation.owner_user_id,
        group_id=conversation.group_id,
    )


def _message_response(message: MessageModel) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
    )
