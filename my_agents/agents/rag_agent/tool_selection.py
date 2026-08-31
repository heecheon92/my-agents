"""Model-backed retrieval-tool selection owned by the RAG Agent."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from my_agents.knowledge.routing import is_comprehensive_document_request
from my_agents.reasoning import openai_reasoning_payload
from my_agents.settings import Settings, get_settings

RAG_AGENT_PLANNER_MODEL = "gpt-5.6-luna"
RAG_AGENT_PLANNER_REASONING_MODE = "standard"
RAG_AGENT_PLANNER_REASONING_EFFORT = "low"

RagRetrievalTool = Literal[
    "search_authorized_chunks",
    "read_authorized_document_comprehensively",
]

_SEARCH_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "search_authorized_chunks",
        "description": (
            "Use focused authorized chunk retrieval for ordinary document questions, targeted "
            "facts, section-specific requests, and summaries that do not require exhaustive "
            "coverage of every part of one document."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

_COMPREHENSIVE_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "read_authorized_document_comprehensively",
        "description": (
            "Use only when the user explicitly or clearly asks to inspect one document "
            "exhaustively: the whole document, every section, beginning to end, without "
            "omissions, all requirements, or cross-section consistency. Do not use merely "
            "because focused retrieval might be weak."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

_TOOLS = (_SEARCH_TOOL, _COMPREHENSIVE_TOOL)
_ALLOWED_TOOLS = frozenset({"search_authorized_chunks", "read_authorized_document_comprehensively"})
_SYSTEM_PROMPT = (
    "You are the retrieval-tool planner for a permission-aware RAG Agent. Choose exactly one "
    "provided tool for the current private-knowledge task. The application, not you, resolves "
    "document identity, authorization, system-knowledge boundaries, character limits, and HITL. "
    "Choose comprehensive reading only for explicit or clearly implied exhaustive document "
    "coverage. Natural multilingual phrasing counts: for example, a named document plus "
    "'without missing anything' and a review/summarize task is comprehensive even if the user "
    "does not literally say 'whole document'. Ordinary summaries and targeted questions use "
    "focused chunk search. Never escalate to comprehensive reading merely because evidence may "
    "be difficult to retrieve."
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RagRetrievalToolDecision:
    """One compact, checkpoint-safe retrieval operation selected for this turn."""

    tool: RagRetrievalTool
    reason: str

    @property
    def comprehensive(self) -> bool:
        return self.tool == "read_authorized_document_comprehensively"


class RagRetrievalToolDecider(Protocol):
    """Runtime-only provider for choosing a RAG retrieval operation."""

    def decide(self, *, messages: Sequence[BaseMessage]) -> RagRetrievalToolDecision:
        """Choose focused or comprehensive retrieval for authorized knowledge."""
        ...


class DeterministicRagRetrievalToolDecider:
    """Credential-free fallback used by tests and deterministic response mode."""

    def decide(self, *, messages: Sequence[BaseMessage]) -> RagRetrievalToolDecision:
        query = _latest_human_text(messages)
        if is_comprehensive_document_request(query):
            return RagRetrievalToolDecision(
                tool="read_authorized_document_comprehensively",
                reason="deterministic comprehensive-document intent",
            )
        return RagRetrievalToolDecision(
            tool="search_authorized_chunks",
            reason="deterministic focused-retrieval fallback",
        )


class OpenAIRagRetrievalToolDecider:
    """Luna-backed RAG tool selector with deterministic failure fallback."""

    def __init__(self, settings: Settings, chat_model: Any | None = None) -> None:
        api_key = settings.openai_api_key_value()
        if chat_model is None and not api_key:
            raise RagAgentPlannerConfigurationError(
                "OPENAI_API_KEY is required for the OpenAI RAG Agent planner"
            )
        model = chat_model or ChatOpenAI(**_build_luna_model_args(settings))
        self._tool_model = model.bind_tools(
            list(_TOOLS),
            tool_choice="required",
            strict=True,
            parallel_tool_calls=False,
        )
        self._fallback = DeterministicRagRetrievalToolDecider()

    def decide(self, *, messages: Sequence[BaseMessage]) -> RagRetrievalToolDecision:
        query = _latest_human_text(messages)
        try:
            response = self._tool_model.invoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=(
                            "Choose the retrieval tool for this private-knowledge task.\n\n"
                            f"Latest user message: {query}\n"
                            f"Recent conversation: {_recent_conversation_text(messages)}"
                        )
                    ),
                ],
                reasoning=openai_reasoning_payload(
                    model=RAG_AGENT_PLANNER_MODEL,
                    mode=RAG_AGENT_PLANNER_REASONING_MODE,
                    effort=RAG_AGENT_PLANNER_REASONING_EFFORT,
                ),
            )
        except Exception as exc:  # provider failures must not break deterministic RAG fallback
            logger.warning("RAG Agent Luna tool selection failed: %s", type(exc).__name__)
            return self._fallback.decide(messages=messages)

        tool = _selected_tool_name(response)
        if tool is None:
            return self._fallback.decide(messages=messages)
        return RagRetrievalToolDecision(
            tool=tool,
            reason=f"Luna selected {tool}",
        )


class RagAgentPlannerConfigurationError(RuntimeError):
    """Raised when the model-backed RAG planner cannot be configured."""


@lru_cache
def get_rag_retrieval_tool_decider() -> RagRetrievalToolDecider:
    """Build the RAG-owned tool selector for the active response mode."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        raise RagAgentPlannerConfigurationError(str(exc)) from exc
    if settings.response_mode == "openai":
        return OpenAIRagRetrievalToolDecider(settings)
    return DeterministicRagRetrievalToolDecider()


def _build_luna_model_args(settings: Settings) -> dict[str, Any]:
    return {
        "model": RAG_AGENT_PLANNER_MODEL,
        "api_key": settings.openai_api_key_value(),
        "timeout": settings.openai_timeout_seconds,
        "max_completion_tokens": 256,
        "use_responses_api": True,
        "output_version": "responses/v1",
    }


def _selected_tool_name(response: object) -> RagRetrievalTool | None:
    tool_calls = getattr(response, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return None
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        name = tool_call.get("name")
        if name in _ALLOWED_TOOLS:
            return name  # type: ignore[return-value]
    return None


def _recent_conversation_text(messages: Sequence[BaseMessage]) -> str:
    lines = []
    for message in messages[-4:]:
        role = getattr(message, "type", "message")
        lines.append(f"{role}: {_message_text(message)}")
    return "\n".join(lines) or "none"


def _latest_human_text(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(parts)
    return str(content)
