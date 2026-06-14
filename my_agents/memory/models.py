"""SQLAlchemy models for opt-in long-term user memory."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from my_agents.persistence.database import Base


class MemoryCategory(StrEnum):
    """Allowed categories for stored long-term memory facts."""

    STABLE_PREFERENCE = "stable_preference"
    PROJECT_CONTEXT = "project_context"
    PERSONAL_FACT = "personal_fact"
    DOCUMENT_DERIVED_FACT = "document_derived_fact"


class MemoryProvenanceType(StrEnum):
    """How a long-term memory record was created."""

    EXPLICIT_USER = "explicit_user"
    ASSISTANT_SUGGESTED = "assistant_suggested"
    AUTO_STORED = "auto_stored"
    DOCUMENT_DERIVED = "document_derived"


class MemoryStatus(StrEnum):
    """Lifecycle state for a durable user memory."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class MemorySensitivity(StrEnum):
    """Sensitivity label assigned before a memory write is persisted."""

    NON_SENSITIVE = "non_sensitive"
    SENSITIVE = "sensitive"


class MemorySuggestionStatus(StrEnum):
    """Lifecycle state for a suggest-confirm memory write."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class UserMemorySettingsModel(Base):
    """Per-user opt-in setting for long-term memory recall and writes."""

    __tablename__ = "user_memory_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_memory_settings_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class UserMemoryModel(Base):
    """LangGraph-store-shaped JSON memory document scoped to one user."""

    __tablename__ = "user_memories"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_memories_user_id_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    namespace_json: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=MemoryStatus.ACTIVE.value, nullable=False, index=True
    )
    sensitivity: Mapped[str] = mapped_column(
        String(20), default=MemorySensitivity.NON_SENSITIVE.value, nullable=False, index=True
    )
    provenance_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    source_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)


class MemorySuggestionModel(Base):
    """Pending suggest-confirm write before it becomes active long-term memory."""

    __tablename__ = "memory_suggestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=MemorySuggestionStatus.PENDING.value, nullable=False, index=True
    )
    sensitivity: Mapped[str] = mapped_column(
        String(20), default=MemorySensitivity.NON_SENSITIVE.value, nullable=False, index=True
    )
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    source_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
