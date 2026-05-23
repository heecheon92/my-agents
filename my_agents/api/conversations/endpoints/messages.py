"""Conversation message endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.serializers import message_response
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_send_prompt
from my_agents.conversations.models import MessageModel, MessageRole
from my_agents.conversations.schemas import MessageCreateRequest, MessageResponse
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

router = APIRouter()


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_message(
    conversation_id: str,
    request: MessageCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    assert_guest_can_send_prompt(db, principal, settings)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.USER.value,
        content=request.content.strip(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message_response(message)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[MessageResponse]:
    """Return the authorized server-owned transcript for a conversation."""
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    messages = db.scalars(
        select(MessageModel)
        .where(MessageModel.conversation_id == conversation_id)
        .order_by(MessageModel.created_at, MessageModel.id)
    ).all()
    return [message_response(message) for message in messages]
