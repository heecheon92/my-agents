"""Graph-owned RAG Agent invocation for the general assistant."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from my_agents.agents.general_assistant.memory_recall import (
    AssistantRuntimeContext,
    latest_human_text,
)
from my_agents.agents.rag_agent import (
    RagAgentRetrievalResult,
    chunks_used_for_answer,
    rag_result_snapshot_for_graph,
    retrieved_context_for_graph,
)
from my_agents.interactions.schemas import INTERACTION_SCHEMA_VERSION
from my_agents.knowledge.routing import RetrievalRoutingDecision


def retrieve_rag_context(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Invoke the RAG Agent retrieval tool from inside the assistant graph."""
    existing_result = state.get("rag_retrieval_snapshot")
    if isinstance(existing_result, Mapping):
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
        "rag_retrieval_snapshot": rag_result_snapshot_for_graph(result),
        "retrieved_chunk_ids": [item.chunk.id for item in used_chunks],
        "retrieval_records": [
            {
                "document_id": item.document.id,
                "chunk_id": item.chunk.id,
                "source": item.source,
                "score": item.score,
            }
            for item in used_chunks
        ],
        "retrieved_context": retrieved_context_for_graph(
            used_chunks,
            hidden_knowledge_base_ids=(
                result.knowledge_base_selection.ambient_system_knowledge_base_ids
            ),
        ),
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


def prepare_document_selection(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Load a bounded, currently authorized option page before interrupting."""
    context = runtime.context or {}
    rag_runtime = context.get("rag_runtime")
    selection_context = context.get("knowledge_base_selection")
    user_id = context.get("user_id") or state.get("principal_id")
    if rag_runtime is None or selection_context is None or not isinstance(user_id, str):
        raise RuntimeError("document selection requires authorized RAG runtime context")
    options, option_count = rag_runtime.document_options(
        user_id=user_id,
        selection_context=selection_context,
        limit=50,
        offset=0,
    )
    return {
        "document_selection_options": [
            {
                "document_id": option.document_id,
                "title": option.title,
                "source_filename": option.source_filename,
                "knowledge_base_id": option.knowledge_base_id,
                "knowledge_base_name": option.knowledge_base_name,
            }
            for option in options
        ],
        "document_selection_option_count": option_count,
    }


def request_document_selection(state: Mapping[str, Any]) -> dict[str, object]:
    """Pause without side effects and accept one selected document ID on resume."""
    run_id = state.get("run_id")
    if not isinstance(run_id, str):
        raise RuntimeError("document selection requires a run_id")
    response = interrupt(
        {
            "schema_version": INTERACTION_SCHEMA_VERSION,
            "interaction_id": f"{run_id}:document_selection",
            "type": "document_selection",
            "reason_code": "ambiguous_document_reference",
            "message_key": "clarification.document_scope.select_source",
            "option_count": state.get("document_selection_option_count", 0),
            "options": state.get("document_selection_options", []),
            "next_cursor": (
                "50" if int(state.get("document_selection_option_count", 0)) > 50 else None
            ),
        }
    )
    if not isinstance(response, Mapping) or not isinstance(response.get("document_id"), str):
        raise ValueError("document selection resume payload requires document_id")
    return {"selected_document_id": response["document_id"]}


def retrieve_selected_rag_context(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Revalidate and retrieve only the document selected by the resumed user."""
    context = runtime.context or {}
    rag_runtime = context.get("rag_runtime")
    selection_context = context.get("knowledge_base_selection")
    user_id = context.get("user_id") or state.get("principal_id")
    conversation_id = state.get("conversation_id")
    selected_document_id = state.get("selected_document_id")
    if (
        rag_runtime is None
        or selection_context is None
        or not isinstance(user_id, str)
        or not isinstance(conversation_id, str)
        or not isinstance(selected_document_id, str)
    ):
        raise RuntimeError("selected document retrieval requires resumable RAG context")
    result = rag_runtime.retrieve_context(
        user_id=user_id,
        conversation_id=conversation_id,
        message=latest_human_text(_state_messages(state)),
        messages=_state_messages(state),
        selection_context=selection_context,
        selected_document_id=selected_document_id,
    )
    return graph_state_from_rag_result(result)


def select_after_rag_context(
    state: Mapping[str, Any], *, document_selection_hitl_enabled: bool = False
) -> str:
    """Route the graph after RAG Agent retrieval."""
    if state.get("rag_halt_before_response") is True:
        return "end"
    snapshot = state.get("rag_retrieval_snapshot")
    if (
        document_selection_hitl_enabled
        and state.get("document_selection_hitl_allowed", True) is True
        and isinstance(snapshot, Mapping)
        and isinstance(snapshot.get("decision"), Mapping)
        and snapshot["decision"].get("route") == "clarification_required"
    ):
        return "prepare_document_selection"
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
