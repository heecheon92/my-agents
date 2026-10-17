"""Versioned admission, recovery, and delivery schemas for server-owned runs."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from my_agents.conversations.schemas import (
    ConversationReplayRequest,
    ConversationRunRequest,
    ConversationRunResponse,
    ReasoningSummaryItem,
)
from my_agents.interactions.schemas import ConversationRunResumeRequestType, PendingInteraction

DurableStatus = Literal[
    "queued", "running", "waiting_for_input", "cancelling", "completed", "failed", "cancelled"
]


class DurableRunRequest(ConversationRunRequest):
    client_request_id: UUID


class DurableResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID
    answer: ConversationRunResumeRequestType


class DurableReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID
    request: ConversationReplayRequest = Field(default_factory=ConversationReplayRequest)


class DurableAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    conversation_id: str
    client_request_id: str
    status: DurableStatus


class DurableError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    detail: str


class DurableActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    event: str
    data: dict[str, Any]


class DurableOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    run_id: str
    conversation_id: str
    client_request_id: str | None
    status: DurableStatus
    cursor: int = Field(ge=0)
    partial_reply: str = ""
    reasoning_summaries: list[ReasoningSummaryItem] = Field(default_factory=list)
    activity_events: list[DurableActivity] = Field(default_factory=list)
    interaction: PendingInteraction | None = None
    result: ConversationRunResponse | None = None
    error: DurableError | None = None


class DurableFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    conversation_id: str
    status: Literal["failed"] = "failed"
    code: str
    detail: str


class DurableCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    conversation_id: str
    status: Literal["cancelled"] = "cancelled"
    partial_reply_persisted: Literal[False] = False
