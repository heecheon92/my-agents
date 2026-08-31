"""Versioned, UI-neutral schemas for agent-requested user input."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

LEGACY_INTERACTION_SCHEMA_VERSION = 1
INTERACTION_SCHEMA_VERSION = 2
DOCUMENT_SELECTION_SHORTLIST_LIMIT = 5
DOCUMENT_SELECTION_REFINEMENT_MAX_ATTEMPTS = 2
DOCUMENT_SELECTION_REFINEMENT_MAX_LENGTH = 120

type InteractionSchemaVersion = Literal[1, 2]
type InteractionType = Literal["document_selection"]


class InteractionReference(BaseModel):
    """Legacy V1 identity retained for already-waiting runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    interaction_id: str = Field(min_length=1, max_length=80)
    type: Literal["document_selection"]


class InteractionReferenceV2(BaseModel):
    """V2 identity for one specific document-selection attempt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    interaction_id: str = Field(min_length=1, max_length=80)
    type: Literal["document_selection"]

    @field_validator("interaction_id")
    @classmethod
    def interaction_id_is_uuid(cls, value: str) -> str:
        UUID(value)
        return value


class DocumentSelectionOption(BaseModel):
    """Display-safe authorized document option for one paused run."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    source_filename: str | None = None
    knowledge_base_id: str | None = None
    knowledge_base_name: str | None = None


class PendingDocumentSelection(InteractionReference):
    """Legacy V1 request for a user to choose one authorized document."""

    reason_code: Literal["ambiguous_document_reference"]
    message_key: Literal["clarification.document_scope.select_source"]
    expires_at: datetime
    option_count: int = Field(ge=0)
    options: list[DocumentSelectionOption] = Field(default_factory=list, max_length=50)
    next_cursor: str | None = None


type DocumentMatchConfidence = Literal["high", "medium", "low"]
type DocumentMatchReasonCode = Literal[
    "exact_title",
    "exact_filename",
    "partial_title",
    "partial_filename",
    "metadata_overlap",
]


class DocumentSelectionOptionV2(DocumentSelectionOption):
    """V2 option with optional shortlist-only relevance metadata."""

    match_confidence: DocumentMatchConfidence | None = None
    match_reason_code: DocumentMatchReasonCode | None = None


class DocumentSelectionRefinement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    attempts_used: int = Field(ge=0, le=DOCUMENT_SELECTION_REFINEMENT_MAX_ATTEMPTS)
    attempts_max: Literal[2] = DOCUMENT_SELECTION_REFINEMENT_MAX_ATTEMPTS
    max_length: Literal[120] = DOCUMENT_SELECTION_REFINEMENT_MAX_LENGTH


class DocumentSelectionBrowse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    cursor: str | None = None


class PendingDocumentSelectionV2(InteractionReferenceV2):
    """Ranked shortlist plus bounded human refinement for one suspended run."""

    reason_code: Literal[
        "ambiguous_document_reference",
        "unresolved_document_reference",
    ]
    message_key: Literal["clarification.document_scope.select_source"]
    expires_at: datetime
    option_count: int = Field(ge=0, le=DOCUMENT_SELECTION_SHORTLIST_LIMIT)
    library_count: int = Field(ge=0)
    options: list[DocumentSelectionOptionV2] = Field(
        default_factory=list,
        max_length=DOCUMENT_SELECTION_SHORTLIST_LIMIT,
    )
    next_cursor: None = None
    refinement: DocumentSelectionRefinement
    browse: DocumentSelectionBrowse


# This alias is the sole backend extension point. Add future semantic interaction
# models here; do not add frontend component or layout contracts to this package.
type PendingInteraction = PendingDocumentSelection | PendingDocumentSelectionV2
pending_interaction_adapter = TypeAdapter(PendingInteraction)


class ConversationRunResumeRequest(InteractionReference):
    """Legacy V1 selection answer."""

    document_id: str = Field(min_length=1, max_length=36)


class ConversationRunSelectRequestV2(InteractionReferenceV2):
    """Choose one document from the V2 shortlist or explicit broad browse."""

    kind: Literal["select"]
    document_id: str = Field(min_length=1, max_length=36)


class ConversationRunRefineRequestV2(InteractionReferenceV2):
    """Supply one bounded filename/reference clue without creating a chat turn."""

    kind: Literal["refine"]
    text: str = Field(min_length=1, max_length=DOCUMENT_SELECTION_REFINEMENT_MAX_LENGTH)

    @field_validator("text")
    @classmethod
    def refinement_is_one_trimmed_line(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or "\n" in stripped or "\r" in stripped:
            raise ValueError("document refinement must be one non-empty line")
        return stripped


type ConversationRunResumeRequestType = (
    ConversationRunResumeRequest | ConversationRunSelectRequestV2 | ConversationRunRefineRequestV2
)
conversation_run_resume_request_adapter = TypeAdapter(ConversationRunResumeRequestType)


class DocumentSelectionOptionsResponse(InteractionReference):
    """Legacy V1 broad option page."""

    option_count: int = Field(ge=0)
    options: list[DocumentSelectionOption] = Field(default_factory=list, max_length=50)
    next_cursor: str | None = None


class DocumentSelectionOptionsResponseV2(InteractionReferenceV2):
    """One explicit broad-library page for an exhausted V2 interaction."""

    mode: Literal["broad"] = "broad"
    option_count: int = Field(ge=0)
    library_count: int = Field(ge=0)
    options: list[DocumentSelectionOptionV2] = Field(default_factory=list, max_length=50)
    next_cursor: str | None = None


type DocumentSelectionOptionsResult = (
    DocumentSelectionOptionsResponse | DocumentSelectionOptionsResponseV2
)
