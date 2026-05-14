"""Typed request and response schemas for the assistant API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RouteLabel = Literal[
    "general_assistant",
    "learning_coach",
    "research_helper",
    "project_planner",
    "career_helper",
]
HistoryRole = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """A single context-only chat history item."""

    model_config = ConfigDict(extra="forbid")

    role: HistoryRole
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only history content before graph execution."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("history content must not be blank")
        return stripped


class ChatRequest(BaseModel):
    """Input payload for POST /assistant/chat."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only messages before graph execution."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class RouteDecision(BaseModel):
    """Deterministic route classification for a request."""

    model_config = ConfigDict(extra="forbid")

    label: RouteLabel
    explanation: str = Field(min_length=1)

    @field_validator("explanation")
    @classmethod
    def explanation_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("route explanation must not be blank")
        return stripped


class ChatResponse(BaseModel):
    """Response payload returned by the personal assistant graph."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1)
    route: RouteDecision
    handled_by: Literal["personal_assistant_graph"] = "personal_assistant_graph"
