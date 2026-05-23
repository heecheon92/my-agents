"""Transcript persistence and replay-pruning helpers."""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from my_agents.conversations.models import (
    AgentEventModel,
    AgentRunModel,
    MessageModel,
    MessageRole,
)
from my_agents.knowledge.models import CitationModel


def persisted_messages_for_conversation(db: Session, conversation_id: str) -> list[MessageModel]:
    return list(
        db.scalars(
            select(MessageModel)
            .where(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at, MessageModel.id)
        ).all()
    )


def messages_for_conversation(db: Session, conversation_id: str) -> list[BaseMessage]:
    return base_messages_from_persisted(persisted_messages_for_conversation(db, conversation_id))


def base_messages_from_persisted(persisted: list[MessageModel]) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    for message in persisted:
        if message.role == MessageRole.ASSISTANT.value:
            messages.append(AIMessage(content=message.content))
        else:
            messages.append(HumanMessage(content=message.content))
    return messages


def preceding_user_message(messages: list[MessageModel]) -> MessageModel | None:
    for message in reversed(messages):
        if message.role == MessageRole.USER.value:
            return message
    return None


def run_for_assistant_message(
    db: Session, conversation_id: str, assistant_message_id: str
) -> AgentRunModel | None:
    return db.scalar(
        select(AgentRunModel)
        .where(
            AgentRunModel.conversation_id == conversation_id,
            AgentRunModel.assistant_message_id == assistant_message_id,
        )
        .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
    )


def prune_conversation_from_message(
    db: Session,
    *,
    conversation_id: str,
    target_message: MessageModel,
    removed_messages: list[MessageModel],
    original_run: AgentRunModel | None,
) -> None:
    removed_message_ids = [message.id for message in removed_messages]
    run_ids_to_prune = set(
        db.scalars(
            select(AgentRunModel.id).where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.assistant_message_id.in_(removed_message_ids),
            )
        ).all()
    )
    if original_run is not None:
        later_run_ids = db.scalars(
            select(AgentRunModel.id).where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.created_at >= original_run.created_at,
            )
        ).all()
        run_ids_to_prune.update(later_run_ids)
    else:
        later_run_ids = db.scalars(
            select(AgentRunModel.id).where(
                AgentRunModel.conversation_id == conversation_id,
                AgentRunModel.created_at >= target_message.created_at,
            )
        ).all()
        run_ids_to_prune.update(later_run_ids)

    if run_ids_to_prune:
        db.execute(delete(CitationModel).where(CitationModel.run_id.in_(run_ids_to_prune)))
        db.execute(delete(AgentEventModel).where(AgentEventModel.run_id.in_(run_ids_to_prune)))
        db.execute(delete(AgentRunModel).where(AgentRunModel.id.in_(run_ids_to_prune)))
    if removed_message_ids:
        db.execute(delete(MessageModel).where(MessageModel.id.in_(removed_message_ids)))
    db.commit()


def store_user_message(db: Session, conversation_id: str, message: str) -> MessageModel:
    user_message = MessageModel(
        conversation_id=conversation_id,
        role=MessageRole.USER.value,
        content=message.strip(),
    )
    db.add(user_message)
    db.commit()
    db.refresh(user_message)
    return user_message
