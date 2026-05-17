"""SQLAlchemy models for organizations/groups and memberships."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from my_agents.persistence.database import Base


class MembershipRole(StrEnum):
    """Supported group membership roles for v1 authorization."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


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
