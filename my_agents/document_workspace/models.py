"""Persistence models for ephemeral conversation attachments and generated artifacts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from my_agents.persistence.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AttachmentStatus(StrEnum):
    AVAILABLE = "available"
    EXPIRED = "expired"
    DELETED = "deleted"


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DELETED = "deleted"
    FAILED = "failed"


class ArtifactStatus(StrEnum):
    AVAILABLE = "available"
    EXPIRED = "expired"
    DELETED = "deleted"


class ConversationAttachmentModel(Base):
    """Metadata for a file uploaded directly to OpenAI for temporary chat use."""

    __tablename__ = "conversation_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="openai")
    provider_file_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AttachmentStatus.AVAILABLE.value, index=True
    )
    provider_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AgentRunAttachmentModel(Base):
    """Many-to-many record of attachments selected for one conversation run."""

    __tablename__ = "agent_run_attachments"
    __table_args__ = (
        UniqueConstraint("run_id", "attachment_id", name="uq_agent_run_attachments_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_attachments.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class DocumentWorkspaceModel(Base):
    """One reusable hosted container for an active conversation."""

    __tablename__ = "document_workspaces"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_document_workspaces_conversation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="openai")
    provider_container_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    mounted_attachment_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    spreadsheet_skill_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WorkspaceStatus.ACTIVE.value, index=True
    )
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ConversationArtifactModel(Base):
    """Downloadable generated file that remains inside an active hosted container."""

    __tablename__ = "conversation_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("document_workspaces.id"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ArtifactStatus.AVAILABLE.value, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class UsageEventModel(Base):
    """Immutable provider-neutral usage observation for later credit settlement."""

    __tablename__ = "usage_events"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_usage_events_idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    capability: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    units_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
