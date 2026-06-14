"""Conversation/source context assembly for the general assistant provider boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage

from my_agents.knowledge.routing import AnswerMode

RECENT_CONVERSATION_MESSAGE_LIMIT = 6
SourceKind = Literal["conversation", "memory", "document", "general_knowledge"]


@dataclass(frozen=True)
class DocumentSourceContext:
    """Authorized document context prepared by the service layer for answer grounding."""

    title: str
    snippet: str
    source_page: object | None = None
    source_filename: str | None = None


@dataclass(frozen=True)
class SourceConflict:
    """Material conflict between two source channels.

    G001 only defines the structure. Later memory/RAG work can populate conflicts when
    memory and document facts are available.
    """

    primary: SourceKind
    secondary: SourceKind
    description: str
    material: bool = True


@dataclass(frozen=True)
class SourceContextBundle:
    """Structured source bundle consumed by response prompt construction.

    Product DB transcript remains the conversation source-of-truth. This bundle selects
    which recent conversation turns enter provider context and keeps other source channels
    explicit for future memory/conflict work.
    """

    recent_conversation: tuple[BaseMessage, ...]
    latest_user_message: str
    answer_mode: AnswerMode
    document_context: tuple[DocumentSourceContext, ...] = ()
    memory_context: tuple[str, ...] = ()
    conflicts: tuple[SourceConflict, ...] = ()
    recent_message_limit: int = RECENT_CONVERSATION_MESSAGE_LIMIT

    @property
    def prior_provider_messages(self) -> tuple[BaseMessage, ...]:
        """Conversation messages to pass before the final instruction prompt."""
        if not self.recent_conversation:
            return ()
        return self.recent_conversation[:-1]


def build_source_context_bundle(
    *,
    messages: Sequence[BaseMessage],
    retrieved_context: Sequence[Mapping[str, Any]] = (),
    memory_context: Sequence[Mapping[str, Any] | str] = (),
    source_conflicts: Sequence[Mapping[str, Any]] = (),
    answer_mode: AnswerMode = "general_knowledge",
    recent_message_limit: int = RECENT_CONVERSATION_MESSAGE_LIMIT,
) -> SourceContextBundle:
    """Build explicit provider context from app-owned transcript and source channels."""
    if recent_message_limit < 1:
        raise ValueError("recent_message_limit must be at least 1")
    recent_conversation = tuple(messages[-recent_message_limit:])
    latest_user_message = _latest_human_text(recent_conversation) or _latest_human_text(messages)
    return SourceContextBundle(
        recent_conversation=recent_conversation,
        latest_user_message=latest_user_message,
        answer_mode=answer_mode,
        document_context=tuple(_document_context(item) for item in retrieved_context),
        memory_context=tuple(_memory_context(item) for item in memory_context),
        conflicts=tuple(_source_conflict(item) for item in source_conflicts),
        recent_message_limit=recent_message_limit,
    )


def format_document_context(bundle: SourceContextBundle) -> str:
    """Format authorized document context for the provider prompt."""
    if not bundle.document_context:
        return "none"
    lines = []
    for index, item in enumerate(bundle.document_context, start=1):
        payload = {
            "title": item.title,
            "snippet": item.snippet,
            "source_filename": item.source_filename,
            "source_page": item.source_page,
        }
        lines.append(f"[{index}] untrusted_document_json={_json_prompt_value(payload)}")
    return "\n".join(lines)


def format_memory_context(bundle: SourceContextBundle) -> str:
    """Format stored memory context for the provider prompt."""
    if not bundle.memory_context:
        return "none"
    return "\n".join(
        f"[{index}] untrusted_memory_json={memory}"
        for index, memory in enumerate(bundle.memory_context, 1)
    )


def format_conflict_context(bundle: SourceContextBundle) -> str:
    """Format material source conflicts for the provider prompt."""
    material_conflicts = [conflict for conflict in bundle.conflicts if conflict.material]
    if not material_conflicts:
        return "none"
    return "\n".join(
        f"[{index}] source_conflict_json={_json_prompt_value(_source_conflict_payload(conflict))}"
        for index, conflict in enumerate(material_conflicts, 1)
    )


def _memory_context(item: Mapping[str, Any] | str) -> str:
    if isinstance(item, str):
        return _json_prompt_value({"content": item.strip()})
    payload = {
        "content": str(item.get("content") or "").strip(),
        "category": item.get("category"),
        "provenance_type": item.get("provenance_type"),
        "source_document_id": item.get("source_document_id"),
    }
    return _json_prompt_value(payload)


def _source_conflict(item: Mapping[str, Any]) -> SourceConflict:
    return SourceConflict(
        primary=_source_kind(item.get("primary")),
        secondary=_source_kind(item.get("secondary")),
        description=str(item.get("description") or "").strip(),
        material=bool(item.get("material", True)),
    )


def _json_prompt_value(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_conflict_payload(conflict: SourceConflict) -> dict[str, object]:
    return {
        "primary": conflict.primary,
        "secondary": conflict.secondary,
        "description": conflict.description,
        "material": conflict.material,
    }


def _source_kind(value: object) -> SourceKind:
    if value in {"conversation", "memory", "document", "general_knowledge"}:
        return value  # type: ignore[return-value]
    return "general_knowledge"


def _document_context(item: Mapping[str, Any]) -> DocumentSourceContext:
    title = str(item.get("title") or "Untitled document")
    snippet = str(item.get("snippet") or "").strip()
    filename = item.get("source_filename")
    return DocumentSourceContext(
        title=title,
        snippet=snippet,
        source_page=item.get("source_page"),
        source_filename=str(filename) if filename else None,
    )


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
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return " ".join(parts)
    return str(content)
