"""SQLAlchemy models for knowledge bases, documents, extraction, and provenance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from my_agents.persistence.database import Base


def _embedding_vector_type() -> Vector:
    """Return pgvector on Postgres and a harmless text column for SQLite tests."""
    return Vector().with_variant(Text(), "sqlite")


DOCUMENT_CHUNK_EMBEDDING_VECTOR_COLUMN = Column(
    "embedding_vector", _embedding_vector_type(), nullable=True
)


class KnowledgeBaseScope(StrEnum):
    """Knowledge-base ownership scope."""

    PERSONAL = "personal"
    GROUP = "group"


class ExtractionStatus(StrEnum):
    """Document extraction run status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class KnowledgePublishRequestStatus(StrEnum):
    """Review lifecycle for personal-to-group knowledge publish requests."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KnowledgeBaseModel(Base):
    """Personal or group knowledge-base container."""

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class DocumentModel(Base):
    """Document metadata for personal or group knowledge bases."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id"), nullable=True, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class DocumentPermissionModel(Base):
    """Explicit user-level document permission grant."""

    __tablename__ = "document_permissions"
    __table_args__ = (UniqueConstraint("document_id", "user_id", name="uq_doc_permission_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    can_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_ingest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class KnowledgePublishRequestModel(Base):
    """Request to publish a personal document as an approved group-owned copy."""

    __tablename__ = "knowledge_publish_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    requester_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_group_id: Mapped[str] = mapped_column(
        ForeignKey("groups.id"), nullable=False, index=True
    )
    target_knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id"), nullable=False, index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=KnowledgePublishRequestStatus.PENDING.value, nullable=False, index=True
    )
    reviewer_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    published_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExtractionRunModel(Base):
    """A deterministic extraction pass over one document."""

    __tablename__ = "extraction_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    relationship_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunkModel(Base):
    """Chunk with deterministic embedding fixture and provenance offsets."""

    __tablename__ = "document_chunks"
    __table_args__ = (DOCUMENT_CHUNK_EMBEDDING_VECTOR_COLUMN,)
    __mapper_args__ = {"exclude_properties": ["embedding_vector"]}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)


class EntityModel(Base):
    """Canonical extracted entity name."""

    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("name", name="uq_entities_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)


class EntityMentionModel(Base):
    """Mention of an entity in a document chunk."""

    __tablename__ = "entity_mentions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=False, index=True
    )
    confidence: Mapped[str] = mapped_column(String(20), default="deterministic", nullable=False)


class EntityRelationshipModel(Base):
    """Relationship between two extracted entities with chunk provenance."""

    __tablename__ = "entity_relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), nullable=False)
    target_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id"), nullable=False, index=True
    )
    extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=False, index=True
    )
    confidence: Mapped[str] = mapped_column(String(20), default="deterministic", nullable=False)


class CitationModel(Base):
    """Citation from a chat run to an authorized document chunk."""

    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id"), nullable=False, index=True
    )
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
