"""Graph-owned long-term memory recall helpers for the general assistant."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime

from my_agents.memory.runtime import MemoryRuntime, memory_item_context


class AssistantRuntimeContext(TypedDict, total=False):
    """Runtime-only dependencies passed to LangGraph outside checkpointed state."""

    user_id: str
    memory_runtime: MemoryRuntime


def retrieve_memory_context(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Retrieve active memory inside the graph and return prompt-safe context updates."""
    context = runtime.context or {}
    user_id = context.get("user_id") or state.get("principal_id")
    memory_runtime = context.get("memory_runtime")
    messages = _state_messages(state)
    retrieved_context = _state_context_list(state.get("retrieved_context"))
    if not isinstance(user_id, str) or memory_runtime is None:
        memory_context: list[dict[str, object]] = []
    else:
        memory_context = [
            memory_item_context(memory)
            for memory in memory_runtime.search(
                user_id=user_id,
                query=latest_human_text(messages),
            )
        ]
    return {
        "memory_context": memory_context,
        "source_conflicts": source_conflicts_for_graph(
            messages=messages,
            memory_context=memory_context,
            retrieved_context=retrieved_context,
        ),
    }


def source_conflicts_for_graph(
    *,
    messages: Sequence[BaseMessage],
    memory_context: Sequence[Mapping[str, object]],
    retrieved_context: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Detect material source conflicts between recent conversation and older memory/docs."""
    conflicts: list[dict[str, object]] = []
    latest_user_text = latest_human_text(messages)
    for memory in memory_context:
        memory_content = str(memory.get("content") or "")
        if _looks_like_user_correction(latest_user_text, memory_content):
            conflicts.append(
                {
                    "primary": "conversation",
                    "secondary": "memory",
                    "description": (
                        "The latest user message appears to revise or contradict stored memory "
                        f"{memory.get('id')}. Prefer the latest conversation unless "
                        "the user confirms the stored memory is still correct."
                    ),
                    "material": True,
                }
            )
    for document in retrieved_context:
        snippet = str(document.get("snippet") or "")
        for memory in memory_context:
            memory_content = str(memory.get("content") or "")
            if _one_side_negates_shared_fact(memory_content, snippet):
                conflicts.append(
                    {
                        "primary": "document",
                        "secondary": "memory",
                        "description": (
                            "Authorized document context appears to conflict with stored memory "
                            f"{memory.get('id')}. Prefer authorized document context for "
                            "document-grounded claims and explain the discrepancy."
                        ),
                        "material": True,
                    }
                )
    return conflicts


def latest_human_text(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) == "human":
            return message_text(message)
    return ""


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return str(content)


def _state_messages(state: Mapping[str, Any]) -> list[BaseMessage]:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, BaseMessage)]


def _state_context_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _looks_like_user_correction(latest_user_text: str, memory_content: str) -> bool:
    latest = latest_user_text.casefold()
    if not latest or not memory_content.strip():
        return False
    correction_markers = ("actually", "no longer", "not", "instead", "changed", "correction")
    if not any(marker in latest for marker in correction_markers):
        return False
    return bool(_meaningful_tokens(latest_user_text) & _meaningful_tokens(memory_content))


def _one_side_negates_shared_fact(left: str, right: str) -> bool:
    shared = _meaningful_tokens(left) & _meaningful_tokens(right)
    if not shared:
        return False
    return _has_negation(left) != _has_negation(right)


def _has_negation(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in (" not ", " no ", "never", "no longer", "without"))


def _meaningful_tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w가-힣]{4,}", text.casefold()))
