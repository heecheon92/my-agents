"""Guest access expiry and public-demo limit enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from my_agents.api.errors import APIErrorCode, APIHTTPException
from my_agents.auth.contracts import Principal
from my_agents.auth.models import UserModel
from my_agents.conversations.models import ConversationModel, MessageModel, MessageRole
from my_agents.knowledge.models import DocumentModel
from my_agents.settings import Settings


def assert_guest_access_active(db: Session, principal: Principal) -> None:
    """Reject expired guest principals while leaving normal users unchanged."""
    if not principal.is_guest:
        return
    user = db.get(UserModel, principal.user_id)
    if (
        user is None
        or user.account_type != "guest"
        or user.guest_expires_at is None
        or _as_utc(user.guest_expires_at) <= datetime.now(UTC)
    ):
        raise APIHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="guest access expired",
            code=APIErrorCode.GUEST_ACCESS_EXPIRED,
        )


def assert_guest_can_create_conversation(
    db: Session,
    principal: Principal,
    settings: Settings,
) -> None:
    """Enforce the public-demo conversation cap for guest principals."""
    assert_guest_access_active(db, principal)
    if not principal.is_guest:
        return
    count = db.scalar(
        select(func.count())
        .select_from(ConversationModel)
        .where(ConversationModel.owner_user_id == principal.user_id)
    )
    if (count or 0) >= settings.guest_max_conversations:
        raise APIHTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="guest conversation limit reached",
            code=APIErrorCode.GUEST_CONVERSATION_LIMIT_REACHED,
        )


def assert_guest_can_send_prompt(
    db: Session,
    principal: Principal,
    settings: Settings,
) -> None:
    """Enforce the public-demo prompt/message cap for guest principals."""
    assert_guest_access_active(db, principal)
    if not principal.is_guest:
        return
    count = db.scalar(
        select(func.count())
        .select_from(MessageModel)
        .join(ConversationModel, MessageModel.conversation_id == ConversationModel.id)
        .where(
            ConversationModel.owner_user_id == principal.user_id,
            MessageModel.role == MessageRole.USER.value,
        )
    )
    if (count or 0) >= settings.guest_max_prompts:
        raise APIHTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="guest prompt limit reached",
            code=APIErrorCode.GUEST_PROMPT_LIMIT_REACHED,
        )


def assert_guest_can_create_document(
    db: Session,
    principal: Principal,
    settings: Settings,
) -> None:
    """Enforce the public-demo document create/upload cap for guest principals."""
    assert_guest_access_active(db, principal)
    if not principal.is_guest:
        return
    count = db.scalar(
        select(func.count())
        .select_from(DocumentModel)
        .where(DocumentModel.owner_user_id == principal.user_id)
    )
    if (count or 0) >= settings.guest_max_document_uploads:
        raise APIHTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="guest document limit reached",
            code=APIErrorCode.GUEST_DOCUMENT_LIMIT_REACHED,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
