"""Versioned, UI-neutral schemas for agent-requested user input."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

INTERACTION_SCHEMA_VERSION = 1

type InteractionSchemaVersion = Literal[1]
type InteractionType = Literal["document_selection"]


class InteractionReference(BaseModel):
    """Common identity shared by every interaction payload and typed answer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    interaction_id: str = Field(min_length=1, max_length=80)
    type: Literal["document_selection"]


class DocumentSelectionOption(BaseModel):
    """Display-safe authorized document option for one paused run."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    source_filename: str | None = None
    knowledge_base_id: str | None = None
    knowledge_base_name: str | None = None


class PendingDocumentSelection(InteractionReference):
    """Semantic request for a user to choose one authorized document."""

    reason_code: Literal["ambiguous_document_reference"]
    message_key: Literal["clarification.document_scope.select_source"]
    expires_at: datetime
    option_count: int = Field(ge=0)
    options: list[DocumentSelectionOption] = Field(default_factory=list, max_length=50)
    next_cursor: str | None = None


# This alias is the sole backend extension point. Add future semantic interaction
# models here; do not add frontend component or layout contracts to this package.
type PendingInteraction = PendingDocumentSelection


class ConversationRunResumeRequest(InteractionReference):
    """Typed answer for one pending document-selection interaction."""

    document_id: str = Field(min_length=1, max_length=36)


class DocumentSelectionOptionsResponse(InteractionReference):
    """One refresh-safe page of currently authorized document options."""

    option_count: int = Field(ge=0)
    options: list[DocumentSelectionOption] = Field(default_factory=list, max_length=50)
    next_cursor: str | None = None
