"""Authorization helpers for conversation API routes."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from my_agents.conversations.models import ConversationModel


def get_authorized_conversation(
    db: Session, conversation_id: str, user_id: str
) -> ConversationModel:
    """Return only conversations owned by the requester."""
    conversation = db.get(ConversationModel, conversation_id)
    if conversation is None or conversation.owner_user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation
