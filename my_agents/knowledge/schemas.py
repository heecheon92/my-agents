"""Pydantic schemas for knowledge bases, documents, extraction, and permissions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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


class DocumentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    group_id: str | None = None
    knowledge_base_id: str | None = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    owner_user_id: str
    group_id: str | None
    knowledge_base_id: str | None


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
    chunk_count: int
    entity_count: int
    relationship_count: int


class ChunkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ordinal: int
    content: str
    start_offset: int
    end_offset: int


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
    chunk_id: str
    snippet: str
