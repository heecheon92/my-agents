"""SQLAlchemy models for organizations/groups, invitations, and memberships."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from my_agents.persistence.database import Base


class MembershipRole(StrEnum):
    """Supported group membership roles for v1 authorization."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class GroupInvitationStatus(StrEnum):
    """Lifecycle states for email-based group invitations."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class GroupModel(Base):
    """Organization/group container for shared knowledge."""

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    memberships: Mapped[list[MembershipModel]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    invitations: Mapped[list[GroupInvitationModel]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )


class MembershipModel(Base):
    """User membership in a group with a coarse role."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_membership_group_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    group: Mapped[GroupModel] = relationship(back_populates="memberships")


class GroupInvitationModel(Base):
    """Pending email invitation for a group.

    Membership rows are intentionally active-only. Pending access is represented here
    until an authenticated user with the invited email accepts the opaque token.
    """

    __tablename__ = "group_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_group_invitations_token_hash"),
        Index(
            "ix_group_invitations_group_status_email",
            "group_id",
            "status",
            "invited_email_normalized",
        ),
        Index(
            "uq_group_invitations_pending_email",
            "group_id",
            "invited_email_normalized",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    invited_email_normalized: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=GroupInvitationStatus.PENDING.value, nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    accepted_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    group: Mapped[GroupModel] = relationship(back_populates="invitations")
