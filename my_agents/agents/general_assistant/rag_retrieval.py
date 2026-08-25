"""Graph-owned RAG Agent invocation for the general assistant."""

from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.agents.general_assistant.memory_recall import (
    AssistantRuntimeContext,
    latest_human_text,
)
from my_agents.agents.rag_agent import (
    RagAgentRetrievalResult,
    chunks_consulted_for_answer,
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
    consulted_chunks = chunks_consulted_for_answer(result)
    return {
        "rag_retrieval_snapshot": rag_result_snapshot_for_graph(result),
        "retrieved_chunk_ids": [item.chunk.id for item in consulted_chunks],
        "retrieval_records": [
            {
                "document_id": item.document.id,
                "chunk_id": item.chunk.id,
                "source": item.source,
                "score": item.score,
            }
            for item in consulted_chunks
        ],
        "retrieved_context": retrieved_context_for_graph(
            consulted_chunks,
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


def resolve_full_document_target(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Resolve one authorized user-controllable document without loading its body."""
    context = runtime.context or {}
    rag_runtime = context.get("rag_runtime")
    selection_context = context.get("knowledge_base_selection")
    user_id = context.get("user_id") or state.get("principal_id")
    if rag_runtime is None or selection_context is None or not isinstance(user_id, str):
        raise RuntimeError("full-document target resolution requires authorized RAG context")
    selected_document_id = state.get("selected_document_id")
    resolution = rag_runtime.resolve_full_document_target(
        user_id=user_id,
        query=latest_human_text(_state_messages(state)),
        selection_context=selection_context,
        selected_document_id=(
            selected_document_id if isinstance(selected_document_id, str) else None
        ),
    )
    if resolution.target is not None:
        return {
            "selected_document_id": resolution.target.document_id,
            "full_document_target_status": "resolved",
            "rag_halt_before_response": False,
        }
    if isinstance(selected_document_id, str):
        return {
            **_full_document_empty_state(
                selection_context=selection_context,
                message=latest_human_text(_state_messages(state)),
                route="retrieval_required",
                reason="selected comprehensive document is no longer authorized",
                insufficient_evidence=True,
            ),
            "full_document_target_status": "unavailable",
        }
    if resolution.option_count > 1:
        return {
            **_full_document_empty_state(
                selection_context=selection_context,
                message=latest_human_text(_state_messages(state)),
                route="clarification_required",
                reason="comprehensive document request has multiple eligible documents",
                insufficient_evidence=False,
            ),
            "full_document_target_status": "ambiguous",
        }
    return {
        **_full_document_empty_state(
            selection_context=selection_context,
            message=latest_human_text(_state_messages(state)),
            route="retrieval_required",
            reason="comprehensive document request has no eligible document",
            insufficient_evidence=True,
        ),
        "full_document_target_status": "unavailable",
    }


def prepare_full_document_read(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
) -> dict[str, object]:
    """Read once to finalize safe coverage/chunk metadata before answer composition."""
    context = runtime.context or {}
    rag_runtime = context.get("rag_runtime")
    selection_context = context.get("knowledge_base_selection")
    user_id = context.get("user_id") or state.get("principal_id")
    document_id = state.get("selected_document_id")
    if (
        rag_runtime is None
        or selection_context is None
        or not isinstance(user_id, str)
        or not isinstance(document_id, str)
    ):
        raise RuntimeError("full-document preparation requires a resolved authorized document")
    started = perf_counter()
    read_result = rag_runtime.read_full_document_range(
        user_id=user_id,
        document_id=document_id,
        selection_context=selection_context,
        full_document_max_chars=int(context.get("full_document_max_chars") or 24_000),
        range_chars=int(context.get("full_document_range_chars") or 12_000),
    )
    latency_ms = round((perf_counter() - started) * 1000, 3)
    if read_result is None or not read_result.content or not read_result.retrieved_chunks:
        return {
            **_full_document_empty_state(
                selection_context=selection_context,
                message=latest_human_text(_state_messages(state)),
                route="retrieval_required",
                reason="full-document text or citation provenance is unavailable",
                insufficient_evidence=True,
            ),
            "full_document_target_status": "unavailable",
        }
    result = RagAgentRetrievalResult(
        decision=RetrievalRoutingDecision(
            route="retrieval_required",
            reason="explicit comprehensive document request",
            rewritten_query=latest_human_text(_state_messages(state)),
            document_scope=(
                "group_documents" if read_result.document.group_id is not None else "user_documents"
            ),
        ),
        answer_mode="document_grounded",
        retrieved_chunks=list(read_result.retrieved_chunks),
        retrieval_latency_ms=latency_ms,
        knowledge_base_selection=selection_context,
        retrieval_evidence=_full_document_evidence(
            chunk_count=len(read_result.retrieved_chunks),
            complete=read_result.complete,
        ),
    )
    graph_state = graph_state_from_rag_result(result)
    graph_state["retrieved_context"] = []
    graph_state.update(
        {
            "document_coverage": {
                "mode": "complete" if read_result.complete else "partial",
                "document_id": read_result.document.id,
                "title": read_result.document.title,
                "source_filename": read_result.document.source_filename,
                "start_offset": read_result.start_offset,
                "end_offset": read_result.end_offset,
                "total_chars": read_result.total_chars,
            },
            "full_document_next_cursor": read_result.next_cursor,
            "full_document_target_status": "resolved",
        }
    )
    return graph_state


def select_after_full_document_target(
    state: Mapping[str, Any], *, document_selection_hitl_enabled: bool = False
) -> str:
    """Route a comprehensive request after deterministic target resolution."""
    status = state.get("full_document_target_status")
    if status == "resolved":
        return "prepare_full_document_read"
    if (
        status == "ambiguous"
        and document_selection_hitl_enabled
        and state.get("document_selection_hitl_allowed", True) is True
    ):
        return "prepare_document_selection"
    if status == "ambiguous":
        return "retrieve_memory"
    return "end"


def select_after_document_selection(state: Mapping[str, Any]) -> str:
    """Resume comprehensive requests at target resolution; keep normal selected RAG."""
    if state.get("full_document_requested") is True:
        return "resolve_full_document_target"
    return "retrieve_selected_rag_context"


def select_after_full_document_read(state: Mapping[str, Any]) -> str:
    """Continue only when bounded full-document evidence is ready."""
    return "end" if state.get("rag_halt_before_response") is True else "retrieve_memory"


def full_document_unavailable_state(
    state: Mapping[str, Any],
    runtime: Runtime[AssistantRuntimeContext],
    *,
    reason: str,
) -> dict[str, object]:
    """Return a safe insufficient-evidence update after a prepared read becomes stale."""
    selection_context = (runtime.context or {}).get("knowledge_base_selection")
    if selection_context is None:
        raise RuntimeError("full-document fallback requires knowledge-base context")
    return {
        **_full_document_empty_state(
            selection_context=selection_context,
            message=latest_human_text(_state_messages(state)),
            route="retrieval_required",
            reason=reason,
            insufficient_evidence=True,
        ),
        "document_coverage": {},
        "full_document_target_status": "unavailable",
    }


def select_after_rag_context(
    state: Mapping[str, Any], *, document_selection_hitl_enabled: bool = False
) -> str:
    """Route the graph after RAG Agent retrieval."""
    snapshot = state.get("rag_retrieval_snapshot")
    if (
        document_selection_hitl_enabled
        and state.get("document_selection_hitl_allowed", True) is True
        and isinstance(snapshot, Mapping)
        and isinstance(snapshot.get("decision"), Mapping)
        and snapshot["decision"].get("route") == "clarification_required"
    ):
        return "prepare_document_selection"
    if state.get("rag_halt_before_response") is True:
        return "end"
    return "retrieve_memory"


def _full_document_empty_state(
    *,
    selection_context: Any,
    message: str,
    route: str,
    reason: str,
    insufficient_evidence: bool,
) -> dict[str, object]:
    result = RagAgentRetrievalResult(
        decision=RetrievalRoutingDecision(
            route=route,  # type: ignore[arg-type]
            reason=reason,
            rewritten_query=message,
            document_scope="unknown",
        ),
        answer_mode="general_knowledge",
        retrieved_chunks=[],
        retrieval_latency_ms=0.0,
        knowledge_base_selection=selection_context,
        retrieval_evidence=_full_document_evidence(chunk_count=0, complete=False),
        retrieval_attempt_count=1,
        insufficient_evidence=insufficient_evidence,
    )
    return graph_state_from_rag_result(result)


def _full_document_evidence(*, chunk_count: int, complete: bool) -> RetrievalEvidence:
    return RetrievalEvidence(
        intent="comprehensive_document",
        candidate_count=chunk_count,
        injected_count=chunk_count,
        rejected_count=0,
        source_counts={"full_document": chunk_count} if chunk_count else {},
        structured_entity_types=(),
        reranker="none",
        budget_truncated=not complete,
    )


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
