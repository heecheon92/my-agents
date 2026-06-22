"""Graph-owned RAG Agent invocation for the general assistant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime

from my_agents.agents.general_assistant.memory_recall import (
    AssistantRuntimeContext,
    latest_human_text,
)
from my_agents.agents.rag_agent import (
    RagAgentRetrievalResult,
    chunks_used_for_answer,
    retrieved_context_for_graph,
)
from my_agents.knowledge.routing import RetrievalRoutingDecision


def retrieve_rag_context(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Invoke the RAG Agent retrieval tool from inside the assistant graph."""
    existing_result = state.get("rag_retrieval_result")
    if isinstance(existing_result, RagAgentRetrievalResult):
        return {}
    context = runtime.context or {}
    rag_runtime = context.get("rag_runtime")
    selection_context = context.get("knowledge_base_selection")
    user_id = context.get("user_id") or state.get("principal_id")
    conversation_id = state.get("conversation_id")
    messages = _state_messages(state)
    if (
        rag_runtime is None
        or selection_context is None
        or not isinstance(user_id, str)
        or not isinstance(conversation_id, str)
    ):
        raise RuntimeError(
            "general_assistant graph requires RAG Agent runtime context; "
            "use graph_context_for_run for conversation runs or build_legacy_chat_graph "
            "for unauthenticated no-KB chat."
        )

    result = rag_runtime.retrieve_context(
        user_id=user_id,
        conversation_id=conversation_id,
        message=latest_human_text(messages),
        messages=messages,
        selection_context=selection_context,
    )
    return graph_state_from_rag_result(result)


def graph_state_from_rag_result(result: RagAgentRetrievalResult) -> dict[str, object]:
    """Return assistant-state fields derived from one RAG Agent retrieval result."""
    used_chunks = chunks_used_for_answer(result)
    return {
        "rag_retrieval_result": result,
        "retrieved_chunk_ids": [item.chunk.id for item in used_chunks],
        "retrieved_context": retrieved_context_for_graph(used_chunks),
        "retrieval_route": result.decision.route,
        "answer_mode": result.answer_mode,
        "document_scope": result.decision.document_scope,
        "rag_halt_before_response": _halts_before_response(result),
    }


def skip_rag_context(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Return an explicit no-retrieval RAG result for bypassed conversation turns."""
    context = runtime.context or {}
    selection_context = context.get("knowledge_base_selection")
    if selection_context is None:
        raise RuntimeError(
            "general_assistant graph requires RAG Agent runtime context; "
            "use graph_context_for_run for conversation runs or build_legacy_chat_graph "
            "for unauthenticated no-KB chat."
        )
    source_decision = state.get("retrieval_source_decision")
    reason = getattr(
        source_decision,
        "reason",
        "source-selection gate bypassed private knowledge-base retrieval",
    )
    result = RagAgentRetrievalResult(
        decision=RetrievalRoutingDecision(
            route="no_retrieval",
            reason=str(reason),
            rewritten_query=latest_human_text(_state_messages(state)),
            document_scope="unknown",
        ),
        answer_mode="general_knowledge",
        retrieved_chunks=[],
        retrieval_latency_ms=0.0,
        knowledge_base_selection=selection_context,
    )
    return graph_state_from_rag_result(result)


def select_after_rag_context(state: Mapping[str, Any]) -> str:
    """Route the graph after RAG Agent retrieval."""
    if state.get("rag_halt_before_response") is True:
        return "end"
    return "retrieve_memory"


def _halts_before_response(result: RagAgentRetrievalResult) -> bool:
    """Return whether retrieval should stop the assistant before reply generation.

    Clarification is still a user-facing assistant response: the RAG Agent can say
    that document scope is ambiguous, but the General Assistant should turn that
    tool result into visible clarification text. Only insufficient evidence halts
    before the responder so the deterministic backend fallback can avoid
    hallucinating document-grounded content.
    """
    return result.insufficient_evidence


def _state_messages(state: Mapping[str, Any]) -> list[BaseMessage]:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, BaseMessage)]
