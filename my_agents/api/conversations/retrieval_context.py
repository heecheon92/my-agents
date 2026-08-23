"""Retrieval routing and graph input helpers for conversation runs."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from rich import print as rich_print
from sqlalchemy.orm import Session

from my_agents.agents.context_forge.contracts import RetrievalEvidence
from my_agents.agents.general_assistant.context import RECENT_CONVERSATION_MESSAGE_LIMIT
from my_agents.agents.rag_agent import (
    RagAgentRetrievalResult,
)
from my_agents.agents.rag_agent import (
    chunks_used_for_answer as rag_chunks_used_for_answer,
)
from my_agents.agents.rag_agent import (
    retrieved_context_for_graph as rag_retrieved_context_for_graph,
)
from my_agents.conversations.schemas import (
    ConversationClarificationRequest,
    DocumentCoverageResponse,
)
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import DocumentChunkModel, DocumentModel
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import (
    AnswerMode,
    RetrievalRoutingDecision,
)
from my_agents.knowledge.source_locations import parse_source_location_json
from my_agents.settings import get_settings

logger = logging.getLogger(__name__)

_INSUFFICIENT_EVIDENCE_REPLY = (
    "I couldn't find enough relevant authorized document evidence to answer that safely. "
    "Please choose or upload the source document and try again."
)


@dataclass(frozen=True)
class ConversationRetrievalContext:
    """Shared retrieval-routing output for sync and streaming run paths."""

    decision: RetrievalRoutingDecision
    answer_mode: AnswerMode
    retrieved_chunks: list[RetrievedChunk]
    retrieval_latency_ms: float
    knowledge_base_selection: KnowledgeBaseSelectionContext
    retrieval_evidence: RetrievalEvidence | None = None
    retrieval_attempt_count: int = 1
    insufficient_evidence: bool = False


def conversation_retrieval_context_from_rag_result(
    result: RagAgentRetrievalResult,
) -> ConversationRetrievalContext:
    """Adapt the RAG Agent public result into the conversation service shape."""
    return ConversationRetrievalContext(
        decision=result.decision,
        answer_mode=result.answer_mode,
        retrieved_chunks=result.retrieved_chunks,
        retrieval_latency_ms=result.retrieval_latency_ms,
        knowledge_base_selection=result.knowledge_base_selection,
        retrieval_evidence=result.retrieval_evidence,
        retrieval_attempt_count=result.retrieval_attempt_count,
        insufficient_evidence=result.insufficient_evidence,
    )


def retrieval_context_from_graph_state(
    graph_state: Mapping[str, object],
    db: Session | None = None,
) -> ConversationRetrievalContext:
    """Extract graph-owned RAG Agent retrieval state after graph execution."""
    result = graph_state.get("rag_retrieval_result")
    if isinstance(result, RagAgentRetrievalResult):
        return conversation_retrieval_context_from_rag_result(result)
    snapshot = graph_state.get("rag_retrieval_snapshot")
    if isinstance(snapshot, Mapping) and db is not None:
        return _retrieval_context_from_snapshot(graph_state, snapshot, db)
    raise RuntimeError("general_assistant graph did not return RAG Agent retrieval context")


def graph_has_retrieval_context(graph_state: Mapping[str, object]) -> bool:
    """Return whether a graph update/final state contains RAG Agent retrieval output."""
    return isinstance(
        graph_state.get("rag_retrieval_result"), RagAgentRetrievalResult
    ) or isinstance(graph_state.get("rag_retrieval_snapshot"), Mapping)


def document_coverage_from_graph_state(
    graph_state: Mapping[str, object],
) -> DocumentCoverageResponse | None:
    """Validate compact coverage metadata returned by the full-document graph path."""
    value = graph_state.get("document_coverage")
    if not isinstance(value, Mapping):
        return None
    return DocumentCoverageResponse.model_validate(dict(value))


def _retrieval_context_from_snapshot(
    graph_state: Mapping[str, object],
    snapshot: Mapping[str, object],
    db: Session,
) -> ConversationRetrievalContext:
    decision_payload = snapshot.get("decision")
    selection_payload = snapshot.get("knowledge_base_selection")
    if not isinstance(decision_payload, Mapping) or not isinstance(selection_payload, Mapping):
        raise RuntimeError("checkpointed RAG metadata is incomplete")
    decision = RetrievalRoutingDecision(
        route=str(decision_payload.get("route") or "no_retrieval"),  # type: ignore[arg-type]
        reason=str(decision_payload.get("reason") or "checkpointed retrieval result"),
        rewritten_query=str(decision_payload.get("rewritten_query") or ""),
        document_scope=str(decision_payload.get("document_scope") or "unknown"),  # type: ignore[arg-type]
    )
    selection = KnowledgeBaseSelectionContext(
        mode=str(selection_payload.get("mode") or "all"),
        knowledge_base_ids=tuple(_string_list(selection_payload.get("knowledge_base_ids"))),
        resolved_count=int(selection_payload.get("resolved_count") or 0),
        resolved_knowledge_base_ids=tuple(
            _string_list(selection_payload.get("resolved_knowledge_base_ids"))
        ),
        ambient_system_knowledge_base_ids=tuple(
            _string_list(selection_payload.get("ambient_system_knowledge_base_ids"))
        ),
        ambient_system_knowledge_base_count=int(
            selection_payload.get("ambient_system_knowledge_base_count") or 0
        ),
    )
    retrieved_chunks: list[RetrievedChunk] = []
    retrieval_records = _mapping_list(graph_state.get("retrieval_records"))
    if not retrieval_records:
        retrieval_records = _mapping_list(graph_state.get("retrieved_context"))
    for item in retrieval_records:
        chunk_id = item.get("chunk_id")
        document_id = item.get("document_id")
        if not isinstance(chunk_id, str) or not isinstance(document_id, str):
            continue
        chunk = db.get(DocumentChunkModel, chunk_id)
        document = db.get(DocumentModel, document_id)
        if chunk is None or document is None or chunk.document_id != document.id:
            continue
        retrieved_chunks.append(
            RetrievedChunk(
                chunk=chunk,
                document=document,
                score=float(item.get("score") or 0.0),
                source=str(item.get("source") or "checkpoint"),
            )
        )
    evidence_payload = snapshot.get("retrieval_evidence")
    evidence = None
    if isinstance(evidence_payload, Mapping):
        evidence = RetrievalEvidence(
            intent=str(evidence_payload.get("intent") or "semantic_qa"),  # type: ignore[arg-type]
            candidate_count=int(evidence_payload.get("candidate_count") or 0),
            injected_count=int(evidence_payload.get("injected_count") or 0),
            rejected_count=int(evidence_payload.get("rejected_count") or 0),
            source_counts={
                str(key): int(value)
                for key, value in (
                    evidence_payload.get("source_counts")
                    if isinstance(evidence_payload.get("source_counts"), Mapping)
                    else {}
                ).items()
            },
            structured_entity_types=tuple(
                _string_list(evidence_payload.get("structured_entity_types"))
            ),
            reranker=str(evidence_payload.get("reranker") or "deterministic"),
            budget_truncated=bool(evidence_payload.get("budget_truncated", False)),
        )
    return ConversationRetrievalContext(
        decision=decision,
        answer_mode=str(snapshot.get("answer_mode") or "general_knowledge"),  # type: ignore[arg-type]
        retrieved_chunks=retrieved_chunks,
        retrieval_latency_ms=float(snapshot.get("retrieval_latency_ms") or 0.0),
        knowledge_base_selection=selection,
        retrieval_evidence=evidence,
        retrieval_attempt_count=int(snapshot.get("retrieval_attempt_count") or 1),
        insufficient_evidence=bool(snapshot.get("insufficient_evidence", False)),
    )


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def graph_input_for_run(
    *,
    messages: list[BaseMessage],
    user_id: str,
    conversation_id: str,
    run_id: str,
    document_selection_hitl_allowed: bool = True,
    preselected_document_id: str | None = None,
    force_full_document_retrieval: bool = False,
) -> dict[str, object]:
    graph_input: dict[str, object] = {
        "messages": messages[-RECENT_CONVERSATION_MESSAGE_LIMIT:],
        "principal_id": user_id,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "document_selection_hitl_allowed": document_selection_hitl_allowed,
        "full_document_retrieval_enabled": (
            get_settings().full_document_retrieval_enabled or force_full_document_retrieval
        ),
        "full_document_requested": False,
        "retrieved_chunk_ids": [],
        "retrieval_records": [],
        "retrieved_context": [],
        # Memory recall is graph-owned. These defaults keep test doubles and legacy
        # graph runners compatible; the real graph overwrites them in `retrieve_memory`.
        "memory_context": [],
        "source_conflicts": [],
        "retrieval_route": "no_retrieval",
        "answer_mode": "general_knowledge",
        "document_scope": "unknown",
        "rag_halt_before_response": False,
    }
    if preselected_document_id is not None:
        graph_input["selected_document_id"] = preselected_document_id
    return graph_input


def insufficient_evidence_reply() -> str:
    """Return a safe answer when required document evidence is unavailable."""
    return _INSUFFICIENT_EVIDENCE_REPLY


def chunks_used_for_answer(
    retrieval_context: ConversationRetrievalContext,
) -> list[RetrievedChunk]:
    return rag_chunks_used_for_answer(_rag_result_from_conversation_context(retrieval_context))


def retrieved_context_for_graph(retrieved_chunks: list[RetrievedChunk]) -> list[dict[str, object]]:
    return rag_retrieved_context_for_graph(retrieved_chunks)


def _rag_result_from_conversation_context(
    retrieval_context: ConversationRetrievalContext,
) -> RagAgentRetrievalResult:
    return RagAgentRetrievalResult(
        decision=retrieval_context.decision,
        answer_mode=retrieval_context.answer_mode,
        retrieved_chunks=retrieval_context.retrieved_chunks,
        retrieval_latency_ms=retrieval_context.retrieval_latency_ms,
        knowledge_base_selection=retrieval_context.knowledge_base_selection,
        retrieval_evidence=retrieval_context.retrieval_evidence,
        retrieval_attempt_count=retrieval_context.retrieval_attempt_count,
        insufficient_evidence=retrieval_context.insufficient_evidence,
    )


def memory_source_snapshot_json(
    *,
    memory_context: list[dict[str, object]],
    source_conflicts: list[dict[str, object]],
) -> str | None:
    """Return a redacted run-audit snapshot for memory-influenced context."""
    if not memory_context and not source_conflicts:
        return None
    snapshot = {
        "memory_count": len(memory_context),
        "conflict_count": len(source_conflicts),
        "memories": [
            {
                "id": memory.get("id"),
                "category": memory.get("category"),
                "provenance_type": memory.get("provenance_type"),
                "source_conversation_id": memory.get("source_conversation_id"),
                "source_message_id": memory.get("source_message_id"),
                "source_run_id": memory.get("source_run_id"),
                "source_document_id": memory.get("source_document_id"),
            }
            for memory in memory_context
        ],
        "conflicts": [
            {
                "primary": conflict.get("primary"),
                "secondary": conflict.get("secondary"),
                "material": conflict.get("material"),
            }
            for conflict in source_conflicts
        ],
    }
    return json.dumps(snapshot, sort_keys=True)


def graph_memory_source_snapshot_json(graph_state: Mapping[str, object]) -> str | None:
    """Return the redacted memory snapshot from graph-owned state, when present."""
    return memory_source_snapshot_json(
        memory_context=_mapping_list(graph_state.get("memory_context")),
        source_conflicts=_mapping_list(graph_state.get("source_conflicts")),
    )


def _mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def log_retrieval_context_for_llm(
    *,
    run_id: str,
    conversation_id: str,
    user_id: str,
    retrieval_context: ConversationRetrievalContext,
    graph_input: dict[str, object],
) -> None:
    """Emit debug-only visibility into KB data selected for LLM context."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    payload = {
        "event": "knowledge_context_injected_to_llm",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "retrieval_route": retrieval_context.decision.route,
        "answer_mode": retrieval_context.answer_mode,
        "document_scope": retrieval_context.decision.document_scope,
        "rewritten_query": retrieval_context.decision.rewritten_query,
        "retrieval_latency_ms": retrieval_context.retrieval_latency_ms,
        "resolved_knowledge_base_ids": list(
            retrieval_context.knowledge_base_selection.resolved_knowledge_base_ids
        ),
        "retrieved_chunk_count": len(retrieval_context.retrieved_chunks),
        "injected_chunk_count": len(graph_input.get("retrieved_context", [])),
        "retrieved_chunks": [
            _debug_chunk_payload(item, snippet_limit=240)
            for item in retrieval_context.retrieved_chunks
        ],
        "injected_context": graph_input.get("retrieved_context", []),
    }
    rich_print(
        "[bold cyan]knowledge context injected to llm[/bold cyan]",
        payload,
    )


def _debug_chunk_payload(item: RetrievedChunk, *, snippet_limit: int) -> dict[str, object]:
    return {
        "document_id": item.document.id,
        "knowledge_base_id": item.document.knowledge_base_id,
        "chunk_id": item.chunk.id,
        "title": item.document.title,
        "source_filename": item.document.source_filename,
        "source_page": item.chunk.source_page,
        "source_location_json": parse_source_location_json(item.chunk.source_location_json),
        "source": item.source,
        "score": item.score,
        "snippet": item.chunk.content[:snippet_limit],
    }


def clarification_request(
    decision: RetrievalRoutingDecision,
) -> ConversationClarificationRequest | None:
    """Return language-neutral HITL state for routes that require user clarification."""
    if decision.route != "clarification_required":
        return None
    return ConversationClarificationRequest(
        retrieval_route=decision.route,
        document_scope=decision.document_scope,
        rewritten_query=decision.rewritten_query,
    )


def clarification_reply(
    base_reply: str | None,
    decision: RetrievalRoutingDecision,
) -> str:
    """Return visible assistant text for a document-scope clarification run.

    The structured `clarification` payload remains the durable HITL contract. This
    reply is a safety net for clients that render assistant text directly, and for
    the General Assistant response provider when it already composed a natural
    clarification in the user's language.
    """
    if decision.route != "clarification_required":
        return (base_reply or "").strip()
    if base_reply and base_reply.strip():
        return base_reply.strip()
    return "Which document or source should I use to answer that?"


def compose_rag_reply(
    base_reply: str,
    retrieved_chunks: list[RetrievedChunk],
    answer_mode: AnswerMode,
) -> str:
    """Return the model reply without prepending clipped retrieval snippets.

    Grounding is exposed through structured citations and graph input context. Injecting
    hard-truncated chunk prefixes into the assistant-visible answer made snippets look
    like broken assistant prose and wasted the UI's citation affordance.
    """
    return base_reply
