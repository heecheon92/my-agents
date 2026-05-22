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


class ConversationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    knowledge_base_selection: KnowledgeBaseSelection = Field(default_factory=KnowledgeBaseSelection)


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
    resolved_knowledge_base_count: int = 0
    citations: list[CitationResponse] = Field(default_factory=list)


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
    resolved_knowledge_base_count: int = 0
    created_at: datetime


class AgentEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict
