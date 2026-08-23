"""Pydantic schemas for conversation and chat-run APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

from my_agents.agents.rag_agent.contracts import RagAgentStageId
from my_agents.document_workspace.schemas import (
    ConversationArtifactResponse,
    ConversationAttachmentResponse,
)
from my_agents.interactions.schemas import PendingDocumentSelection
from my_agents.knowledge.routing import AnswerMode, DocumentScope, RetrievalRoute
from my_agents.knowledge.schemas import CitationResponse, KnowledgeBaseSelection
from my_agents.schemas import RouteDecision
from my_agents.settings import ReasoningEffort, ReasoningMode


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


class AgentTraceEvidence(BaseModel):
    """Closed allowlist of display-safe evidence fields for agent trace stages."""

    model_config = ConfigDict(extra="forbid")

    retrieval_route: RetrievalRoute | None = None
    answer_mode: AnswerMode | None = None
    document_scope: DocumentScope | None = None
    intent: str | None = Field(default=None, max_length=160)
    structured_entity_types: list[str] | None = None
    resolved_knowledge_base_count: int | None = Field(default=None, ge=0)
    candidate_count: int | None = Field(default=None, ge=0)
    authorized_context_count: int | None = Field(default=None, ge=0)
    reranker: str | None = Field(default=None, max_length=160)
    injected_count: int | None = Field(default=None, ge=0)
    rejected_count: int | None = Field(default=None, ge=0)
    budget_truncated: bool | None = None
    route_label: str | None = Field(default=None, max_length=160)
    retrieved_chunk_count: int | None = Field(default=None, ge=0)
    citation_count: int | None = Field(default=None, ge=0)
    reply_length: int | None = Field(default=None, ge=0)
    clarification_required: bool | None = None

    @model_serializer(mode="wrap")
    def serialize_compact_evidence(self, serializer):  # noqa: ANN001, ANN201
        """Omit fields that do not belong to this trace stage."""
        return {name: value for name, value in serializer(self).items() if value is not None}


class AgentTraceStep(BaseModel):
    """Frontend-safe localized trace step without hidden chain-of-thought."""

    model_config = ConfigDict(extra="forbid")

    id: RagAgentStageId
    event_type: Literal["retrieval_completed", "graph_invoked", "answer_composed"]
    status: Literal["completed", "skipped", "waiting", "failed"]
    title: AgentTraceText
    description: AgentTraceText
    evidence: AgentTraceEvidence = Field(default_factory=AgentTraceEvidence)


class ConversationRunWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["regeneration_sources_unavailable"]
    message: str
    missing_document_ids: list[str] = Field(default_factory=list)
    missing_source_filenames: list[str] = Field(default_factory=list)


class DocumentCoverageResponse(BaseModel):
    """Refresh-safe disclosure for one bounded comprehensive document read."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["complete", "partial"]
    document_id: str
    title: str
    source_filename: str | None = None
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    total_chars: int = Field(ge=0)


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
    reasoning_mode: ReasoningMode | None = None
    reasoning_effort: ReasoningEffort | None = None
    knowledge_base_selection: KnowledgeBaseSelection = Field(default_factory=KnowledgeBaseSelection)
    attachment_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(
        default_factory=list,
        max_length=10,
    )

    @field_validator("attachment_ids")
    @classmethod
    def attachment_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("attachment_ids must be unique")
        return value


class ConversationReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_selection: KnowledgeBaseSelection | None = None
    reasoning_mode: ReasoningMode | None = None
    reasoning_effort: ReasoningEffort | None = None


class ConversationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed"] = "completed"
    run_id: str
    conversation_id: str
    reply: str
    route: RouteDecision
    handled_by: str
    reasoning_mode: ReasoningMode = "standard"
    reasoning_effort: ReasoningEffort = "medium"
    retrieval_route: RetrievalRoute
    answer_mode: AnswerMode
    document_scope: DocumentScope
    knowledge_base_selection: KnowledgeBaseSelection
    resolved_knowledge_base_ids: list[str] = Field(default_factory=list)
    resolved_knowledge_base_count: int = 0
    citations: list[CitationResponse] = Field(default_factory=list)
    document_coverage: DocumentCoverageResponse | None = None
    warnings: list[ConversationRunWarning] = Field(default_factory=list)
    clarification: ConversationClarificationRequest | None = None
    agent_trace: list[AgentTraceStep] = Field(default_factory=list)
    attachments: list[ConversationAttachmentResponse] = Field(default_factory=list)
    artifacts: list[ConversationArtifactResponse] = Field(default_factory=list)


class ConversationRunInterruptedResponse(BaseModel):
    """Refresh-safe response for a graph waiting on user input."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["waiting_for_input"] = "waiting_for_input"
    run_id: str
    conversation_id: str
    interaction: PendingDocumentSelection


type ConversationRunResult = ConversationRunResponse | ConversationRunInterruptedResponse


class ConversationRunCancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: str
    status: Literal[
        "running", "waiting_for_input", "cancelling", "cancelled", "completed", "failed"
    ]


class AgentRunSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: str
    status: str
    route_label: str | None
    reasoning_mode: ReasoningMode
    reasoning_effort: ReasoningEffort
    knowledge_base_selection: KnowledgeBaseSelection
    resolved_knowledge_base_ids: list[str] = Field(default_factory=list)
    resolved_knowledge_base_count: int = 0
    created_at: datetime


class AgentEventPayload(BaseModel):
    """Base for persisted event payloads exposed through the display-safe API."""

    model_config = ConfigDict(extra="forbid")


class KnowledgeSelectionEventPayload(AgentEventPayload):
    knowledge_base_selection: KnowledgeBaseSelection = Field(default_factory=KnowledgeBaseSelection)
    resolved_knowledge_base_ids: list[str] = Field(default_factory=list)
    resolved_knowledge_base_count: int = Field(default=0, ge=0)


class RunStartedEventPayload(KnowledgeSelectionEventPayload):
    run_id: str
    conversation_id: str
    status: Literal["running"] = "running"
    reasoning_mode: ReasoningMode = "standard"
    reasoning_effort: ReasoningEffort = "medium"


class UserMessageStoredEventPayload(AgentEventPayload):
    message_id: str
    content_length: int = Field(ge=0)


class RetrievalCompletedEventPayload(KnowledgeSelectionEventPayload):
    retrieval_route: RetrievalRoute = "no_retrieval"
    answer_mode: AnswerMode = "general_knowledge"
    document_scope: DocumentScope = "unknown"
    authorized_context_count: int = Field(default=0, ge=0)
    semantic_vector_count: int = Field(default=0, ge=0)
    keyword_match_count: int = Field(default=0, ge=0)
    document_metadata_count: int = Field(default=0, ge=0)
    document_metadata_profile_count: int = Field(default=0, ge=0)
    graph_expansion_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    retrieval_attempt_count: int = Field(default=1, ge=1)
    retrieval_retry_count: int = Field(default=0, ge=0)
    insufficient_evidence: bool = False
    candidate_count: int | None = Field(default=None, ge=0)
    injected_count: int | None = Field(default=None, ge=0)
    rejected_count: int | None = Field(default=None, ge=0)
    structured_entity_count: int | None = Field(default=None, ge=0)
    structured_entity_types: list[str] = Field(default_factory=list)
    budget_truncated: bool | None = None
    contextforge_intent: str | None = Field(default=None, max_length=160)
    contextforge_reranker: str | None = Field(default=None, max_length=160)
    agent_trace: list[AgentTraceStep] = Field(default_factory=list)


class FullDocumentReadEventPayload(AgentEventPayload):
    mode: Literal["complete", "partial"]
    document_id: str
    title: str
    source_filename: str | None = None
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    total_chars: int = Field(ge=0)
    latency_ms: float = Field(default=0, ge=0)


class GraphInvokedEventPayload(KnowledgeSelectionEventPayload):
    route_label: str = Field(default="general_assistant", max_length=160)
    retrieval_route: RetrievalRoute = "no_retrieval"
    answer_mode: AnswerMode = "general_knowledge"
    document_scope: DocumentScope = "unknown"
    message_count: int = Field(default=0, ge=0)
    retrieved_chunk_count: int = Field(default=0, ge=0)
    memory_count: int | None = Field(default=None, ge=0)
    memory_conflict_count: int | None = Field(default=None, ge=0)
    memory_categories: list[str] = Field(default_factory=list)
    memory_provenance_types: list[str] = Field(default_factory=list)
    agent_trace: list[AgentTraceStep] = Field(default_factory=list)


class AnswerComposedEventPayload(KnowledgeSelectionEventPayload):
    citation_count: int = Field(default=0, ge=0)
    reply_length: int = Field(default=0, ge=0)
    retrieval_route: RetrievalRoute = "no_retrieval"
    answer_mode: AnswerMode = "general_knowledge"
    document_scope: DocumentScope = "unknown"
    clarification_required: bool = False
    insufficient_evidence: bool = False
    agent_trace: list[AgentTraceStep] = Field(default_factory=list)


class AttachmentsReadyEventPayload(AgentEventPayload):
    attachment_count: int = Field(ge=1)
    total_bytes: int = Field(ge=1)


class DocumentWorkspaceStartedEventPayload(AgentEventPayload):
    attachment_count: int = Field(ge=1)
    workspace_expires_at: datetime


class ArtifactCreatedEventPayload(AgentEventPayload):
    artifact_id: str
    filename: str = Field(max_length=512)
    content_type: str = Field(max_length=255)
    byte_size: int | None = Field(default=None, ge=0)
    expires_at: datetime


class RunCancelRequestedEventPayload(AgentEventPayload):
    run_id: str
    status: Literal["cancelling"] = "cancelling"


class RunInterruptedEventPayload(AgentEventPayload):
    run_id: str
    status: Literal["waiting_for_input"] = "waiting_for_input"
    interaction_id: str
    interaction_schema_version: Literal[1]
    interaction_type: Literal["document_selection"] = "document_selection"
    option_count: int = Field(ge=0)
    expires_at: datetime


class RunResumedEventPayload(AgentEventPayload):
    run_id: str
    status: Literal["running"] = "running"
    interaction_id: str
    interaction_schema_version: Literal[1]
    interaction_type: Literal["document_selection"] = "document_selection"


class RunCancelledEventPayload(AgentEventPayload):
    run_id: str
    conversation_id: str | None = None
    status: Literal["cancelled"] = "cancelled"
    partial_reply_persisted: bool = False
    stale_active_run_cleanup: Literal[True] | None = None


class RunFailedEventPayload(AgentEventPayload):
    safe_error_type: str = Field(default="RunFailed", max_length=160)
    safe_reason: str | None = Field(default=None, max_length=160)
    stale_active_run_cleanup: Literal[True] | None = None


class AgentEventResponseBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    sequence: int


class RunStartedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["run_started"]
    payload: RunStartedEventPayload


class UserMessageStoredAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["user_message_stored"]
    payload: UserMessageStoredEventPayload


class RetrievalCompletedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["retrieval_completed"]
    payload: RetrievalCompletedEventPayload


class FullDocumentReadAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["full_document_read"]
    payload: FullDocumentReadEventPayload


class GraphInvokedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["graph_invoked"]
    payload: GraphInvokedEventPayload


class AnswerComposedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["answer_composed"]
    payload: AnswerComposedEventPayload


class AttachmentsReadyAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["attachments_ready"]
    payload: AttachmentsReadyEventPayload


class DocumentWorkspaceStartedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["document_workspace_started"]
    payload: DocumentWorkspaceStartedEventPayload


class ArtifactCreatedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["artifact_created"]
    payload: ArtifactCreatedEventPayload


class RunCancelRequestedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["run_cancel_requested"]
    payload: RunCancelRequestedEventPayload


class RunInterruptedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["run_interrupted"]
    payload: RunInterruptedEventPayload


class RunResumedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["run_resumed"]
    payload: RunResumedEventPayload


class RunCancelledAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["run_cancelled"]
    payload: RunCancelledEventPayload


class RunFailedAgentEventResponse(AgentEventResponseBase):
    event_type: Literal["run_failed"]
    payload: RunFailedEventPayload


type AgentEventResponse = Annotated[
    RunStartedAgentEventResponse
    | UserMessageStoredAgentEventResponse
    | RetrievalCompletedAgentEventResponse
    | FullDocumentReadAgentEventResponse
    | GraphInvokedAgentEventResponse
    | AttachmentsReadyAgentEventResponse
    | DocumentWorkspaceStartedAgentEventResponse
    | ArtifactCreatedAgentEventResponse
    | AnswerComposedAgentEventResponse
    | RunInterruptedAgentEventResponse
    | RunResumedAgentEventResponse
    | RunCancelRequestedAgentEventResponse
    | RunCancelledAgentEventResponse
    | RunFailedAgentEventResponse,
    Field(discriminator="event_type"),
]
