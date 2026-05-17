"""Pydantic schemas for group and membership APIs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from my_agents.groups.models import MembershipRole


class GroupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class GroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: MembershipRole


class MemberUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    role: MembershipRole


class MemberPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MembershipRole
