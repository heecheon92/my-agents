"""Authorization helpers for conversation API routes."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.conversations.models import ConversationModel
from my_agents.groups.models import MembershipModel


def get_authorized_conversation(
    db: Session, conversation_id: str, user_id: str
) -> ConversationModel:
    conversation = db.get(ConversationModel, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    if conversation.owner_user_id == user_id:
        return conversation
    if conversation.group_id is not None and has_group_membership(
        db, conversation.group_id, user_id
    ):
        return conversation
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")


def require_group_membership(db: Session, group_id: str, user_id: str) -> None:
    if not has_group_membership(db, group_id, user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not allowed")


def has_group_membership(db: Session, group_id: str, user_id: str) -> bool:
    return (
        db.scalar(
            select(MembershipModel).where(
                MembershipModel.group_id == group_id,
                MembershipModel.user_id == user_id,
            )
        )
        is not None
    )
