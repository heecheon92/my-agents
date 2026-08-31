"""Pydantic schemas for knowledge bases, documents, extraction, and permissions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from my_agents.knowledge.models import (
    KnowledgeBasePurpose,
    KnowledgeBaseScope,
    KnowledgePublishRequestStatus,
)


class KnowledgeBaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    scope: KnowledgeBaseScope = KnowledgeBaseScope.PERSONAL
    group_id: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    scope: KnowledgeBaseScope
    owner_user_id: str
    group_id: str | None
    purpose: KnowledgeBasePurpose = KnowledgeBasePurpose.STANDARD
    published_group_ids: list[str] = Field(default_factory=list)


class KnowledgeBaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class KnowledgePublishRequestCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document_id: str | None = Field(default=None, min_length=1)
    target_knowledge_base_id: str | None = Field(default=None, min_length=1)
    source_knowledge_base_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_publish_shape(self) -> KnowledgePublishRequestCreateRequest:
        has_document_source = self.source_document_id is not None
        has_knowledge_base_source = self.source_knowledge_base_id is not None
        if has_document_source == has_knowledge_base_source:
            raise ValueError("submit exactly one personal document or personal knowledge base")
        if has_document_source and self.target_knowledge_base_id is None:
            raise ValueError("document publish requests require target_knowledge_base_id")
        if has_knowledge_base_source and self.target_knowledge_base_id is not None:
            raise ValueError("knowledge-base publish requests target the group, not a group KB")
        return self


class KnowledgePublishRequestSourceDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    content: str
    source_type: str = "text"
    source_filename: str | None = None
    source_content_type: str | None = None
    source_byte_size: int | None = None
    source_page_count: int | None = None
    parser_name: str | None = None
    created_at: datetime


class KnowledgePublishRequestSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    source_kind: Literal["document", "knowledge_base"]
    source_knowledge_base_id: str | None = None
    source_knowledge_base_name: str | None = None
    documents: list[KnowledgePublishRequestSourceDocumentResponse] = Field(default_factory=list)


class KnowledgePublishRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    requester_user_id: str
    target_group_id: str
    target_knowledge_base_id: str | None
    source_document_id: str | None
    source_knowledge_base_id: str | None
    source_document_title: str | None = None
    source_document_excerpt: str | None = None
    source_document_filename: str | None = None
    source_knowledge_base_name: str | None = None
    target_knowledge_base_name: str | None = None
    status: KnowledgePublishRequestStatus
    reviewer_user_id: str | None
    published_document_id: str | None
    published_knowledge_base_id: str | None
    published_knowledge_base_name: str | None = None
    created_at: datetime
    reviewed_at: datetime | None


class KnowledgeBaseSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["all", "selected"] = "all"
    knowledge_base_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection_shape(self) -> KnowledgeBaseSelection:
        if self.mode == "selected" and not self.knowledge_base_ids:
            raise ValueError("selected knowledge_base_ids must be non-empty")
        if self.mode == "all" and self.knowledge_base_ids:
            raise ValueError("all knowledge-base mode does not accept knowledge_base_ids")
        return self


class DocumentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    group_id: str | None = None
    knowledge_base_id: str | None = Field(default=None, min_length=1)


class DocumentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = None

    @model_validator(mode="after")
    def validate_update_shape(self) -> DocumentUpdateRequest:
        if self.title is None and self.content is None:
            raise ValueError("submit title or content")
        return self


class DocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    owner_user_id: str
    group_id: str | None
    knowledge_base_id: str | None
    source_type: str = "text"
    source_filename: str | None = None
    source_content_type: str | None = None
    source_byte_size: int | None = None
    source_sha256: str | None = None
    source_page_count: int | None = None
    parser_name: str | None = None


class DocumentPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    content: str
    source_type: str = "text"
    source_filename: str | None = None
    source_content_type: str | None = None
    source_byte_size: int | None = None
    source_page_count: int | None = None
    parser_name: str | None = None
    created_at: datetime


class DocumentPermissionPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    can_read: bool = True
    can_write: bool = False
    can_manage: bool = False
    can_ingest: bool = False


class DocumentPermissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    user_id: str
    can_read: bool
    can_write: bool
    can_manage: bool
    can_ingest: bool


class ExtractionRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    status: str
    stage: str | None = None
    progress_percent: int = Field(default=0, ge=0, le=100)
    chunk_count: int
    entity_count: int
    relationship_count: int
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ChunkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ordinal: int
    content: str
    start_offset: int
    end_offset: int
    source_page: int | None = None
    source_location_json: dict[str, object] | None = None


class EntityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class RelationshipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    chunk_id: str


class CitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    document_title: str | None = None
    knowledge_base_id: str | None = None
    knowledge_base_name: str | None = None
    chunk_id: str
    snippet: str
    source_page: int | None = None
    source_location_json: dict[str, object] | None = None
    source_filename: str | None = None
