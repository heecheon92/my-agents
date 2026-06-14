"""Retrieval routing and graph input helpers for conversation runs."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from rich import print as rich_print
from sqlalchemy.orm import Session

from my_agents.agents.context_forge import invoke_context_forge_graph
from my_agents.agents.context_forge.contracts import ContextForgeRequest, RetrievalEvidence
from my_agents.conversations.schemas import ConversationClarificationRequest
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import (
    AnswerMode,
    RetrievalRoutingDecision,
    is_relevant_retrieval_result,
)
from my_agents.knowledge.source_locations import parse_source_location_json

logger = logging.getLogger(__name__)

_RETRIEVED_CONTEXT_SNIPPET_CHARS = 1200
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


def prepare_retrieval_context(
    *,
    db: Session,
    user_id: str,
    conversation_id: str,
    message: str,
    messages: list[BaseMessage],
    selection_context: KnowledgeBaseSelectionContext,
) -> ConversationRetrievalContext:
    graph_result = invoke_context_forge_graph(
        db=db,
        request=ContextForgeRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            query=message,
            messages=messages,
            selection_context=selection_context,
        ),
    )
    result = graph_result.result
    return ConversationRetrievalContext(
        decision=result.decision,
        answer_mode=result.answer_mode,
        retrieved_chunks=result.retrieved_chunks,
        retrieval_latency_ms=result.retrieval_latency_ms,
        knowledge_base_selection=selection_context,
        retrieval_evidence=result.evidence,
        retrieval_attempt_count=graph_result.retrieval_attempt_count,
        insufficient_evidence=graph_result.insufficient_evidence,
    )


def graph_input_for_run(
    *,
    messages: list[BaseMessage],
    user_id: str,
    conversation_id: str,
    retrieval_context: ConversationRetrievalContext,
) -> dict[str, object]:
    used_chunks = chunks_used_for_answer(retrieval_context)
    retrieved_context = retrieved_context_for_graph(used_chunks)
    return {
        "messages": messages,
        "principal_id": user_id,
        "conversation_id": conversation_id,
        "retrieved_chunk_ids": [item.chunk.id for item in used_chunks],
        "retrieved_context": retrieved_context,
        # Memory recall is graph-owned. These defaults keep test doubles and legacy
        # graph runners compatible; the real graph overwrites them in `retrieve_memory`.
        "memory_context": [],
        "source_conflicts": [],
        "retrieval_route": retrieval_context.decision.route,
        "answer_mode": retrieval_context.answer_mode,
        "document_scope": retrieval_context.decision.document_scope,
    }


def insufficient_evidence_reply() -> str:
    """Return a safe answer when required document evidence is unavailable."""
    return _INSUFFICIENT_EVIDENCE_REPLY


def chunks_used_for_answer(
    retrieval_context: ConversationRetrievalContext,
) -> list[RetrievedChunk]:
    if retrieval_context.answer_mode == "general_knowledge":
        return []
    return [
        item
        for item in retrieval_context.retrieved_chunks
        if is_relevant_retrieval_result(
            route=retrieval_context.decision.route,
            source=item.source,
            score=item.score,
        )
    ]


def retrieved_context_for_graph(retrieved_chunks: list[RetrievedChunk]) -> list[dict[str, object]]:
    return [
        {
            "document_id": item.document.id,
            "chunk_id": item.chunk.id,
            "knowledge_base_id": item.document.knowledge_base_id,
            "title": item.document.title,
            "snippet": item.chunk.content[:_RETRIEVED_CONTEXT_SNIPPET_CHARS],
            "source_page": item.chunk.source_page,
            "source_location_json": parse_source_location_json(item.chunk.source_location_json),
            "source_filename": item.document.source_filename,
            "source": item.source,
        }
        for item in retrieved_chunks
    ]


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
