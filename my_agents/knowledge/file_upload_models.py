"""Short-lived direct-upload admissions; object keys never cross the public API."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from my_agents.persistence.database import Base


class DocumentUploadSessionModel(Base):
    __tablename__ = "document_upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stored_file_id: Mapped[str] = mapped_column(ForeignKey("stored_files.id"), nullable=False)
    staging_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("stored_files.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    ingestion_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    staging_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    extraction_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
