"""Private file ownership, reserved capacity, and durable object erasure."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from my_agents.persistence.database import Base


class StoredFileModel(Base):
    """One independently owned immutable original, including unfinished uploads."""

    __tablename__ = "stored_files"
    __table_args__ = (
        UniqueConstraint("bucket", "object_key"),
        CheckConstraint("byte_size >= 0", name="stored_file_byte_size_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved", index=True)
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class StorageQuotaModel(Base):
    """Atomic account budget counting pending uploads and independently owned copies."""

    __tablename__ = "storage_quota"
    __table_args__ = (
        CheckConstraint("id = 1", name="storage_quota_singleton"),
        CheckConstraint("used_bytes >= 0", name="storage_quota_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class StorageCleanupModel(Base):
    """Retryable erasure outbox retained until provider deletion succeeds."""

    __tablename__ = "storage_cleanup"
    __table_args__ = (UniqueConstraint("bucket", "object_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_file_id: Mapped[str | None] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    not_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
