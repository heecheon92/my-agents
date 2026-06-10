"""Pydantic schemas for group, invitation, and membership APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from my_agents.groups.models import GroupInvitationStatus, MembershipRole


class GroupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class GroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: MembershipRole


class MemberPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MembershipRole


class MemberResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str
    user_id: str
    role: MembershipRole
    created_at: datetime


class GroupInvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: MembershipRole


class GroupInvitationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MembershipRole


class GroupInvitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    group_id: str
    invited_email: EmailStr
    role: MembershipRole
    status: GroupInvitationStatus
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    cancelled_at: datetime | None = None
    resent_at: datetime | None = None


class GroupInvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=512)
