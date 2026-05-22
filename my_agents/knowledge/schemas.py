"""Pydantic schemas for knowledge bases, documents, extraction, and permissions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from my_agents.knowledge.models import KnowledgeBaseScope


class KnowledgeBaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    scope: KnowledgeBaseScope = KnowledgeBaseScope.PERSONAL
    group_id: str | None = None


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    scope: KnowledgeBaseScope
    owner_user_id: str
    group_id: str | None


class KnowledgeBaseSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["all", "selected"] = "all"
    knowledge_base_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection_shape(self) -> "KnowledgeBaseSelection":
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
    knowledge_base_id: str = Field(min_length=1)


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
    knowledge_base_id: str | None = None
    chunk_id: str
    snippet: str
    source_page: int | None = None
    source_filename: str | None = None
