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
HistoryItem = dict[str, str]


class ChatRequest(BaseModel):
    """Input payload for POST /assistant/chat."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    history: list[HistoryItem] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only messages before graph execution."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped

    @field_validator("history")
    @classmethod
    def history_must_use_supported_message_shape(
        cls,
        value: list[HistoryItem],
    ) -> list[HistoryItem]:
        """Validate simple JSON history before converting it to LangChain messages."""
        normalized: list[HistoryItem] = []
        for item in value:
            if set(item) != {"role", "content"}:
                raise ValueError("history items must contain exactly role and content")
            role = item["role"]
            if role not in ("user", "assistant"):
                raise ValueError("history role must be user or assistant")
            content = item["content"].strip()
            if not content:
                raise ValueError("history content must not be blank")
            normalized.append({"role": role, "content": content})
        return normalized


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
