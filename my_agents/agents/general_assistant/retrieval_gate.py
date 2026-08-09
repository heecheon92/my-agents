"""Thin source-selection gate before graph-owned RAG retrieval."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from my_agents.agents.general_assistant.memory_recall import latest_human_text, message_text
from my_agents.agents.general_assistant.responders import ResponseProviderConfigurationError
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.routing import route_retrieval
from my_agents.reasoning import openai_reasoning_payload
from my_agents.settings import Settings, get_settings

RetrievalSource = Literal["knowledge_base", "bypass"]

_SYSTEM_PROMPT = (
    "You are a source-selection gate for a FastAPI + LangGraph assistant. Decide whether "
    "the graph should run private knowledge-base retrieval before answering. Return only "
    'compact JSON shaped as {"source":"knowledge_base|bypass","reason":"..."}.\n\n'
    "Consider the latest user message together with recent conversation context. Choose "
    "knowledge_base only when the current turn asks about uploaded/saved documents, "
    "knowledge bases, internal project/system knowledge, user/group documents, or facts "
    "likely stored in authorized private sources. Choose bypass when the user explicitly "
    "says not to use saved docs/knowledge bases, asks for common knowledge, asks for "
    "web/current/external information, or when private retrieval is merely optional. "
    "If the latest message is a follow-up to a recent web/current/external request and "
    "does not introduce a new private-KB/document intent, keep using bypass. If the latest "
    "message explicitly changes source, obey the latest message. "
    "If uncertain, choose bypass so the response provider can answer from general knowledge "
    "or hosted web search."
)

_EXPLICIT_BYPASS_HINTS = (
    "do not use knowledge base",
    "don't use knowledge base",
    "dont use knowledge base",
    "no knowledge base",
    "without knowledge base",
    "without using knowledge base",
    "do not use saved docs",
    "don't use saved docs",
    "dont use saved docs",
    "do not search saved docs",
    "don't search saved docs",
    "dont search saved docs",
    "do not look at saved docs",
    "don't look at saved docs",
    "do not use uploaded documents",
    "don't use uploaded documents",
    "without using uploaded documents",
    "don't look at my documents",
    "do not look at my documents",
    "지식베이스 사용하지",
    "지식 베이스 사용하지",
    "저장된 문서 사용하지",
    "저장 문서 사용하지",
    "문서 검색하지",
    "문서 찾아보지",
    "업로드한 문서 사용하지",
    "업로드 문서 사용하지",
)
_CURRENT_OR_WEB_HINTS = (
    "latest",
    "current",
    "currently",
    "recent",
    "today",
    "news",
    "web",
    "internet",
    "online",
    "search the web",
    "웹",
    "인터넷",
    "최신",
    "최근",
    "뉴스",
)
_KNOWN_PRIVATE_PHRASE_HINTS = (
    "smoke-test phrase",
    "verification token",
)
_SOURCE_LOOKUP_VERBS = (
    "mention",
    "mentions",
    "mentioned",
    "cite",
    "cites",
    "depend on",
    "depends on",
    "compare",
)
_FOLLOW_UP_HINTS = (
    "what about",
    "how about",
    "and ",
    "also ",
    "then ",
    "same ",
    "that ",
    "those ",
    "it ",
    "they ",
    "since then",
    "그럼",
    "그러면",
    "그건",
    "그거",
    "그것",
    "이건",
    "이거",
    "저건",
    "저거",
    "그 다음",
    "다음은",
    "또",
)


@dataclass(frozen=True)
class RetrievalSourceDecision:
    """Decision for whether this turn should enter KB-backed RAG retrieval."""

    source: RetrievalSource
    reason: str


class RetrievalSourceDecider(Protocol):
    """Runtime-only provider for source selection."""

    def decide(
        self,
        *,
        messages: Sequence[BaseMessage],
        selection_context: KnowledgeBaseSelectionContext,
    ) -> RetrievalSourceDecision:
        """Return whether to call the RAG Agent retrieval runtime."""
        ...


class DeterministicRetrievalSourceDecider:
    """Offline source-selection fallback for tests and deterministic mode."""

    def decide(
        self,
        *,
        messages: Sequence[BaseMessage],
        selection_context: KnowledgeBaseSelectionContext,
    ) -> RetrievalSourceDecision:
        query = latest_human_text(messages)
        if _explicitly_bypasses_knowledge_base(query):
            return RetrievalSourceDecision(
                source="bypass",
                reason="latest user message explicitly asks not to use saved documents",
            )
        if _explicitly_prefers_current_or_web(query):
            return RetrievalSourceDecision(
                source="bypass",
                reason="latest user message asks for current or web-backed information",
            )
        if _selection_context_is_explicit(selection_context):
            return RetrievalSourceDecision(
                source="knowledge_base",
                reason="request includes an explicit selected knowledge-base scope",
            )
        if _looks_like_private_identifier_lookup(query) and _has_retrievable_sources(
            selection_context
        ):
            return RetrievalSourceDecision(
                source="knowledge_base",
                reason="latest user message asks about a distinctive term likely stored in KB",
            )
        if _looks_like_source_lookup(query) and _has_retrievable_sources(selection_context):
            return RetrievalSourceDecision(
                source="knowledge_base",
                reason="latest user message asks for source lookup over available KBs",
            )
        decision = route_retrieval(
            message=query,
            history=list(messages[:-1]),
            authorized_document_count=None,
        )
        if decision.route in {"retrieval_required", "clarification_required"}:
            return RetrievalSourceDecision(
                source="knowledge_base",
                reason=decision.reason,
            )
        if _continues_recent_current_or_web_context(messages):
            return RetrievalSourceDecision(
                source="bypass",
                reason=(
                    "latest user message appears to continue a recent current/web-backed "
                    "conversation without introducing a private knowledge-base need"
                ),
            )
        if decision.route == "retrieval_optional":
            return RetrievalSourceDecision(
                source="knowledge_base",
                reason=decision.reason,
            )
        return RetrievalSourceDecision(
            source="bypass",
            reason=(
                "no explicit private knowledge-base need detected by deterministic "
                "source-selection policy"
            ),
        )


class OpenAIRetrievalSourceDecider:
    """LLM-backed multilingual gate for KB-vs-general/web source selection."""

    def __init__(self, settings: Settings, chat_model: Any | None = None) -> None:
        api_key = settings.openai_api_key_value()
        if chat_model is None and not api_key:
            raise ResponseProviderConfigurationError(
                "OPENAI_API_KEY is required when MY_AGENTS_RESPONSE_MODE=openai"
            )
        self._settings = settings
        self._chat_model = chat_model or ChatOpenAI(**_build_gate_model_args(settings))
        self._fallback = DeterministicRetrievalSourceDecider()

    def decide(
        self,
        *,
        messages: Sequence[BaseMessage],
        selection_context: KnowledgeBaseSelectionContext,
    ) -> RetrievalSourceDecision:
        query = latest_human_text(messages)
        if _explicitly_bypasses_knowledge_base(query):
            return RetrievalSourceDecision(
                source="bypass",
                reason="latest user message explicitly asks not to use saved documents",
            )

        response = self._chat_model.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        "Decide the source for this assistant turn.\n\n"
                        f"Latest user message: {query}\n"
                        f"Recent conversation: {_recent_conversation_text(messages)}\n"
                        "Knowledge-base selection context: "
                        f"{_selection_context_payload(selection_context)}"
                    )
                ),
            ],
            reasoning=openai_reasoning_payload(
                model=self._settings.openai_model,
                mode="standard",
                effort=self._settings.openai_reasoning_effort,
            ),
        )
        parsed = _parse_decision_response(_message_content_text(response))
        if parsed is not None:
            return parsed
        return self._fallback.decide(messages=messages, selection_context=selection_context)


@lru_cache
def get_retrieval_source_decider() -> RetrievalSourceDecider:
    """Build the source-selection provider selected by environment settings."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        raise ResponseProviderConfigurationError(str(exc)) from exc
    if settings.response_mode == "openai":
        return OpenAIRetrievalSourceDecider(settings)
    return DeterministicRetrievalSourceDecider()


def _build_gate_model_args(settings: Settings) -> dict[str, Any]:
    args: dict[str, Any] = {
        "model": settings.openai_model,
        "api_key": settings.openai_api_key_value(),
        "timeout": settings.openai_timeout_seconds,
        "max_completion_tokens": 128,
        "use_responses_api": True,
        "output_version": "responses/v1",
    }
    if settings.openai_verbosity is not None:
        args["verbosity"] = settings.openai_verbosity
    return args


def _explicitly_bypasses_knowledge_base(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    return any(hint in normalized for hint in _EXPLICIT_BYPASS_HINTS)


def _explicitly_prefers_current_or_web(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    return any(hint in normalized for hint in _CURRENT_OR_WEB_HINTS)


def _selection_context_is_explicit(selection_context: KnowledgeBaseSelectionContext) -> bool:
    return selection_context.mode == "selected" and (
        bool(selection_context.knowledge_base_ids)
        or bool(selection_context.resolved_knowledge_base_ids)
    )


def _has_retrievable_sources(selection_context: KnowledgeBaseSelectionContext) -> bool:
    return (
        selection_context.resolved_count
        + selection_context.ambient_system_knowledge_base_count
        + len(selection_context.resolved_knowledge_base_ids)
    ) > 0


def _looks_like_private_identifier_lookup(message: str) -> bool:
    normalized = message.casefold()
    if any(hint in normalized for hint in _KNOWN_PRIVATE_PHRASE_HINTS):
        return True
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", message)
    return any(_is_distinctive_identifier(token) for token in tokens)


def _looks_like_source_lookup(message: str) -> bool:
    normalized = message.casefold()
    return any(verb in normalized for verb in _SOURCE_LOOKUP_VERBS)


def _continues_recent_current_or_web_context(messages: Sequence[BaseMessage]) -> bool:
    query = latest_human_text(messages)
    if not query or not _looks_like_follow_up(query):
        return False
    return any(
        isinstance(message, HumanMessage)
        and _explicitly_prefers_current_or_web(message_text(message))
        for message in messages[:-1][-4:]
    )


def _looks_like_follow_up(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    if any(normalized.startswith(hint) for hint in _FOLLOW_UP_HINTS):
        return True
    token_count = len(normalized.split())
    return token_count <= 8 and message.strip().endswith("?")


def _is_distinctive_identifier(token: str) -> bool:
    if "-" in token or "_" in token:
        return True
    if any(character.isdigit() for character in token):
        return True
    return any(character.islower() for character in token) and any(
        character.isupper() for character in token[1:]
    )


def _parse_decision_response(text: str) -> RetrievalSourceDecision | None:
    payload_text = _extract_json_object(text)
    if payload_text is None:
        return None
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    source = payload.get("source")
    reason = str(payload.get("reason") or "").strip()[:240]
    if source not in {"knowledge_base", "bypass"}:
        return None
    return RetrievalSourceDecision(
        source=source,
        reason=reason or "LLM source-selection gate",
    )


def _extract_json_object(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return stripped[start : end + 1]


def _recent_conversation_text(messages: Sequence[BaseMessage]) -> str:
    recent = messages[-4:]
    lines = []
    for message in recent:
        role = getattr(message, "type", "message")
        lines.append(f"{role}: {message_text(message)}")
    return "\n".join(lines) or "none"


def _selection_context_payload(selection_context: KnowledgeBaseSelectionContext) -> str:
    payload = {
        "mode": selection_context.mode,
        "requested_count": len(selection_context.knowledge_base_ids),
        "resolved_count": selection_context.resolved_count,
        "ambient_system_count": selection_context.ambient_system_knowledge_base_count,
    }
    return json.dumps(payload, sort_keys=True)


def _message_content_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)
