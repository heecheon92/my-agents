"""Pydantic schemas for group, invitation, and membership APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from my_agents.auth.schemas import UserResponse
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
    nickname: str
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


class GroupInvitationSignupRequest(BaseModel):
    """Create an invited account from a token-proved email identity."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=512)
    nickname: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("nickname", mode="before")
    @classmethod
    def nickname_must_not_be_blank(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("nickname must not be blank")
        return stripped

    @field_validator("password")
    @classmethod
    def password_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("password must not be blank")
        return value


class GroupInvitationSignupResponse(BaseModel):
    """Browser login envelope for invitation-token account creation."""

    model_config = ConfigDict(extra="forbid")

    user: UserResponse
    member: MemberResponse
    csrf_token: str = Field(min_length=1)
