"""Shared helpers for graph spies that need to simulate RAG Agent retrieval."""

from __future__ import annotations

from typing import Any

from my_agents.agents.general_assistant.memory_recall import latest_human_text
from my_agents.agents.general_assistant.rag_retrieval import graph_state_from_rag_result


def rag_update_for_spy(input: dict[str, Any], kwargs: dict[str, Any]) -> dict[str, object]:
    """Run the graph-owned RAG retrieval node for lightweight graph test doubles."""
    if "rag_retrieval_result" in input:
        raise AssertionError("graph input must not be seeded with precomputed RAG retrieval")
    context = kwargs.get("context") or {}
    if "rag_runtime" not in context or "conversation_id" not in input:
        return {}
    messages = input["messages"]
    result = context["rag_runtime"].retrieve_context(
        user_id=context["user_id"],
        conversation_id=input["conversation_id"],
        message=latest_human_text(messages),
        messages=messages,
        selection_context=context["knowledge_base_selection"],
    )
    return graph_state_from_rag_result(result)
