"""Pydantic API schemas for long-term memory management."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from my_agents.memory.models import (
    MemoryCategory,
    MemoryProvenanceType,
    MemorySensitivity,
    MemoryStatus,
    MemorySuggestionStatus,
)


class UserMemorySettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    updated_at: datetime


class UserMemorySettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class UserMemoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    category: MemoryCategory

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("memory content must not be blank")
        return stripped


class UserMemorySuggestionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    category: MemoryCategory

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("memory suggestion content must not be blank")
        return stripped


class UserMemoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    namespace: list[str]
    key: str
    category: MemoryCategory
    content: str
    value: dict
    status: MemoryStatus
    sensitivity: MemorySensitivity
    provenance_type: MemoryProvenanceType
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    source_run_id: str | None = None
    source_document_id: str | None = None
    created_at: datetime
    updated_at: datetime
    deactivated_at: datetime | None = None
    deleted_at: datetime | None = None
    stale_at: datetime | None = None
    stale_reason: str | None = None


class UserMemorySuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: MemoryCategory
    content: str
    value: dict
    status: MemorySuggestionStatus
    sensitivity: MemorySensitivity
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    source_run_id: str | None = None
    source_document_id: str | None = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None
    memory_id: str | None = None
