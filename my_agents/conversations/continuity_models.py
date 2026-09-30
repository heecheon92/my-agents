"""Conversation-owned derived context; never a replacement for the transcript."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from my_agents.persistence.database import Base


class ConversationSummaryModel(Base):
    __tablename__ = "conversation_summaries"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    covered_message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    body_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class MessageAttachmentModel(Base):
    __tablename__ = "message_attachments"
    __table_args__ = (
        UniqueConstraint("message_id", "attachment_id", name="uq_message_attachment_pair"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), nullable=False, index=True)
    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_attachments.id"), nullable=False, index=True
    )


class AttachmentNoteModel(Base):
    __tablename__ = "attachment_notes"
    __table_args__ = (UniqueConstraint("run_id", "attachment_id", name="uq_attachment_note_run"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_attachments.id"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    body_json: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderCleanupModel(Base):
    """Opaque cleanup targets intentionally survive deletion of the conversation."""

    __tablename__ = "provider_cleanup"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
