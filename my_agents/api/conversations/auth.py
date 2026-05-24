"""Authorization helpers for conversation API routes."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.conversations.models import ConversationModel
from my_agents.groups.models import MembershipModel


def get_authorized_conversation(
    db: Session, conversation_id: str, user_id: str
) -> ConversationModel:
    """Return only conversations owned by the requester.

    `ConversationModel.group_id` is a source-context pointer for group knowledge,
    not a transcript-sharing grant. Group members must never gain access to
    another member's private chat transcript, runs, events, or replay surface.
    """
    conversation = db.get(ConversationModel, conversation_id)
    if conversation is None or conversation.owner_user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation


def require_conversation_source_membership(
    db: Session, conversation: ConversationModel, user_id: str
) -> None:
    """Require current membership before mutating/running a group-context chat."""
    if conversation.group_id is not None:
        require_group_membership(db, conversation.group_id, user_id)


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
