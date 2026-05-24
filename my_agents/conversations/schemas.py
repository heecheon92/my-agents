"""Pydantic schemas for conversation and chat-run APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute
from my_agents.knowledge.schemas import CitationResponse, KnowledgeBaseSelection
from my_agents.schemas import RouteDecision


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Untitled conversation", min_length=1, max_length=200)
    group_id: str | None = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    owner_user_id: str
    group_id: str | None


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    role: str
    content: str


class ConversationRunWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["regeneration_sources_unavailable"]
    message: str
    missing_document_ids: list[str] = Field(default_factory=list)
    missing_source_filenames: list[str] = Field(default_factory=list)


class ConversationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    knowledge_base_selection: KnowledgeBaseSelection = Field(default_factory=KnowledgeBaseSelection)
    optional_personal_knowledge_base_ids: list[str] = Field(default_factory=list)


class ConversationReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_selection: KnowledgeBaseSelection | None = None
    optional_personal_knowledge_base_ids: list[str] = Field(default_factory=list)


class ConversationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: str
    reply: str
    route: RouteDecision
    handled_by: str
    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    knowledge_base_selection: KnowledgeBaseSelection
    source_context_group_id: str | None = None
    mandatory_group_knowledge_base_ids: list[str] = Field(default_factory=list)
    mandatory_group_knowledge_base_count: int = 0
    optional_personal_knowledge_base_ids: list[str] = Field(default_factory=list)
    optional_personal_knowledge_base_count: int = 0
    resolved_knowledge_base_ids: list[str] = Field(default_factory=list)
    resolved_knowledge_base_count: int = 0
    citations: list[CitationResponse] = Field(default_factory=list)
    warnings: list[ConversationRunWarning] = Field(default_factory=list)


class ConversationRunCancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: str
    status: Literal["running", "cancelling", "cancelled", "completed", "failed"]


class AgentRunSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: str
    status: str
    route_label: str | None
    knowledge_base_selection: KnowledgeBaseSelection
    source_context_group_id: str | None = None
    mandatory_group_knowledge_base_ids: list[str] = Field(default_factory=list)
    mandatory_group_knowledge_base_count: int = 0
    optional_personal_knowledge_base_ids: list[str] = Field(default_factory=list)
    optional_personal_knowledge_base_count: int = 0
    resolved_knowledge_base_ids: list[str] = Field(default_factory=list)
    resolved_knowledge_base_count: int = 0
    created_at: datetime


class AgentEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict
