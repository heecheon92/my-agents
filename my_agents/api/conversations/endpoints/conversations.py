"""Conversation collection and item endpoints."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.api.conversations.auth import get_authorized_conversation, require_group_membership
from my_agents.api.conversations.serializers import conversation_response
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import (
    assert_guest_access_active,
    assert_guest_can_create_conversation,
)
from my_agents.conversations.models import ConversationModel
from my_agents.conversations.schemas import ConversationCreateRequest, ConversationResponse
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings


def create_conversation(
    request: ConversationCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConversationResponse:
    assert_guest_can_create_conversation(db, principal, settings)
    if request.group_id is not None:
        require_group_membership(db, request.group_id, principal.user_id)
    conversation = ConversationModel(
        owner_user_id=principal.user_id,
        group_id=request.group_id,
        title=request.title.strip(),
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation_response(conversation)


def list_conversations(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[ConversationResponse]:
    assert_guest_access_active(db, principal)
    conversations = db.scalars(
        select(ConversationModel).where(ConversationModel.owner_user_id == principal.user_id)
    ).all()
    return [conversation_response(conversation) for conversation in conversations]


def get_conversation(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ConversationResponse:
    assert_guest_access_active(db, principal)
    conversation = get_authorized_conversation(db, conversation_id, principal.user_id)
    return conversation_response(conversation)
