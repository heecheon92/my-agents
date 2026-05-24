"""SQLAlchemy models for server-owned conversations and chat runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from my_agents.persistence.database import Base


class MessageRole(StrEnum):
    """Persisted conversation message roles."""

    USER = "user"
    ASSISTANT = "assistant"


class RunStatus(StrEnum):
    """Chat run lifecycle status."""

    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentEventType(StrEnum):
    """Frontend-visible agent activity events without hidden chain-of-thought."""

    RUN_STARTED = "run_started"
    USER_MESSAGE_STORED = "user_message_stored"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    GRAPH_INVOKED = "graph_invoked"
    ANSWER_COMPOSED = "answer_composed"
    RUN_CANCEL_REQUESTED = "run_cancel_requested"
    RUN_CANCELLED = "run_cancelled"
    RUN_FAILED = "run_failed"


class ConversationModel(Base):
    """Server-owned conversation scope."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("groups.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Untitled conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class MessageModel(Base):
    """Persisted server-owned conversation message."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AgentRunModel(Base):
    """Persisted chat run boundary for frontend-visible service calls."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    route_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    route_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_route: Mapped[str | None] = mapped_column(String(40), nullable=True)
    answer_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    document_scope: Mapped[str | None] = mapped_column(String(40), nullable=True)
    knowledge_base_selection_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    selected_knowledge_base_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_context_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    mandatory_group_knowledge_base_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    optional_personal_knowledge_base_ids_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    resolved_knowledge_base_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_knowledge_base_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    assistant_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AgentEventModel(Base):
    """Structured, redacted activity event for one chat run."""

    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
