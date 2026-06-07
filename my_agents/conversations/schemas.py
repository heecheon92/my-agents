"""Pydantic schemas for conversation and chat-run APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute
from my_agents.knowledge.schemas import CitationResponse, KnowledgeBaseSelection
from my_agents.schemas import RouteDecision


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Untitled conversation", min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    owner_user_id: str


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    role: str
    content: str


class AgentTraceText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    en: str
    ko: str


class AgentTraceStep(BaseModel):
    """Frontend-safe localized trace step without hidden chain-of-thought."""

    model_config = ConfigDict(extra="forbid")

    id: str
    event_type: str
    status: Literal["completed", "skipped", "waiting", "failed"]
    title: AgentTraceText
    description: AgentTraceText
    evidence: dict[str, Any] = Field(default_factory=dict)


class ConversationRunWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["regeneration_sources_unavailable"]
    message: str
    missing_document_ids: list[str] = Field(default_factory=list)
    missing_source_filenames: list[str] = Field(default_factory=list)


class ConversationClarificationRequest(BaseModel):
    """Language-neutral clarification contract for human-in-the-loop replies."""

    model_config = ConfigDict(extra="forbid")

    required: Literal[True] = True
    kind: Literal["document_scope"] = "document_scope"
    reason_code: Literal["ambiguous_document_reference"] = "ambiguous_document_reference"
    message_key: Literal["clarification.document_scope.select_source"] = (
        "clarification.document_scope.select_source"
    )
    input_slot: Literal["document_reference"] = "document_reference"
    retrieval_route: RetrievalRoute
    document_scope: DocumentScope
    rewritten_query: str | None = None


class ConversationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    knowledge_base_selection: KnowledgeBaseSelection = Field(default_factory=KnowledgeBaseSelection)


class ConversationReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_selection: KnowledgeBaseSelection | None = None


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
    resolved_knowledge_base_ids: list[str] = Field(default_factory=list)
    resolved_knowledge_base_count: int = 0
    citations: list[CitationResponse] = Field(default_factory=list)
    warnings: list[ConversationRunWarning] = Field(default_factory=list)
    clarification: ConversationClarificationRequest | None = None
    agent_trace: list[AgentTraceStep] = Field(default_factory=list)


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
