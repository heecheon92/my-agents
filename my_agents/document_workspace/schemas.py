"""Public schemas for the temporary document workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DocumentFormatCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension: str
    category: str
    mime_types: list[str]
    analysis_supported: bool
    artifact_status: Literal["certified", "unavailable"]


class DocumentWorkspaceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_files_per_run: int
    max_combined_bytes: int
    workspace_idle_ttl_seconds: int


class DocumentWorkspaceCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    eligible: bool
    reason_code: str | None = None
    provider: Literal["openai"] = "openai"
    model: str
    registry_verified_at: str
    limits: DocumentWorkspaceLimits
    formats: list[DocumentFormatCapability]
    consent_required: Literal[True] = True
    retention: Literal["ephemeral"] = "ephemeral"


class ConversationAttachmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    filename: str
    content_type: str
    extension: str
    category: str
    byte_size: int
    status: Literal["available", "expired", "deleted"]
    expires_at: datetime
    created_at: datetime


class ConversationArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    conversation_id: str
    filename: str
    content_type: str
    extension: str
    byte_size: int | None
    status: Literal["available", "expired", "deleted"]
    download_url: str
    expires_at: datetime
    created_at: datetime


class DocumentWorkspaceExecutionResult(BaseModel):
    """Graph-state-safe result returned by the runtime adapter."""

    model_config = ConfigDict(extra="forbid")

    reply: str
    artifacts: list[ConversationArtifactResponse] = Field(default_factory=list)
    workspace_expires_at: datetime
    reasoning_summary: str | None = Field(default=None, max_length=500)
