"""Response serializers for conversation API SQLAlchemy models."""

from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.conversations.models import AgentRunModel, ConversationModel, MessageModel
from my_agents.conversations.schemas import (
    AgentRunSummaryResponse,
    ConversationResponse,
    ConversationRunResponse,
    MessageResponse,
)
from my_agents.knowledge.auth import KnowledgeBaseSelectionContext
from my_agents.knowledge.models import CitationModel, DocumentChunkModel, DocumentModel
from my_agents.knowledge.retrieval import RetrievedChunk
from my_agents.knowledge.routing import AnswerMode, RetrievalRoutingDecision
from my_agents.knowledge.schemas import CitationResponse, KnowledgeBaseSelection
from my_agents.schemas import RouteDecision


def coerce_route(route: RouteDecision | dict) -> RouteDecision:
    if isinstance(route, RouteDecision):
        return route
    return RouteDecision.model_validate(route)


def knowledge_base_selection_response(
    selection_context: KnowledgeBaseSelectionContext,
) -> KnowledgeBaseSelection:
    return KnowledgeBaseSelection(
        mode=selection_context.mode,
        knowledge_base_ids=list(selection_context.knowledge_base_ids),
    )


def knowledge_base_selection_payload(
    selection_context: KnowledgeBaseSelectionContext,
) -> dict[str, object]:
    return {
        "knowledge_base_selection": knowledge_base_selection_response(selection_context).model_dump(
            mode="json"
        ),
        "resolved_knowledge_base_count": selection_context.resolved_count,
    }


def run_knowledge_base_selection(run: AgentRunModel) -> KnowledgeBaseSelection:
    raw_ids = run.selected_knowledge_base_ids_json or "[]"
    try:
        parsed = json.loads(raw_ids)
    except json.JSONDecodeError:
        parsed = []
    ids = [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []
    return KnowledgeBaseSelection(
        mode=run.knowledge_base_selection_mode or "all",
        knowledge_base_ids=ids,
    )


def conversation_response(conversation: ConversationModel) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        owner_user_id=conversation.owner_user_id,
        group_id=conversation.group_id,
    )


def run_summary_response(run: AgentRunModel) -> AgentRunSummaryResponse:
    return AgentRunSummaryResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        route_label=run.route_label,
        knowledge_base_selection=run_knowledge_base_selection(run),
        resolved_knowledge_base_count=run.resolved_knowledge_base_count,
        created_at=run.created_at,
    )


def run_detail_response(db: Session, run: AgentRunModel) -> ConversationRunResponse:
    if run.route_label is None or run.route_explanation is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is not completed")
    if run.assistant_message_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run reply is unavailable")
    assistant_message = db.get(MessageModel, run.assistant_message_id)
    if assistant_message is None or assistant_message.conversation_id != run.conversation_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run reply is unavailable")
    citations = db.scalars(
        select(CitationModel).where(CitationModel.run_id == run.id).order_by(CitationModel.id)
    ).all()
    return ConversationRunResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        reply=assistant_message.content,
        route=RouteDecision(label=run.route_label, explanation=run.route_explanation),
        handled_by="personal_assistant_graph",
        retrieval_route=run.retrieval_route or "no_retrieval",
        answer_mode=run.answer_mode or "general_knowledge",
        document_scope=run.document_scope or "unknown",
        knowledge_base_selection=run_knowledge_base_selection(run),
        resolved_knowledge_base_count=run.resolved_knowledge_base_count,
        citations=[citation_response(db, citation) for citation in citations],
    )


def completed_run_response(
    *,
    run: AgentRunModel,
    reply: str,
    route: RouteDecision,
    retrieval_decision: RetrievalRoutingDecision,
    answer_mode: AnswerMode,
    selection_context: KnowledgeBaseSelectionContext,
    citations: list[CitationModel],
    retrieved_chunks: list[RetrievedChunk],
) -> ConversationRunResponse:
    return ConversationRunResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        reply=reply,
        route=route,
        handled_by="personal_assistant_graph",
        retrieval_route=retrieval_decision.route,
        answer_mode=answer_mode,
        document_scope=retrieval_decision.document_scope,
        knowledge_base_selection=knowledge_base_selection_response(selection_context),
        resolved_knowledge_base_count=selection_context.resolved_count,
        citations=[
            CitationResponse(
                id=citation.id,
                document_id=citation.document_id,
                knowledge_base_id=item.document.knowledge_base_id,
                chunk_id=citation.chunk_id,
                snippet=citation.snippet,
                source_page=item.chunk.source_page,
                source_filename=item.document.source_filename,
            )
            for citation, item in zip(citations, retrieved_chunks, strict=True)
        ],
    )


def citation_response(db: Session, citation: CitationModel) -> CitationResponse:
    chunk = db.get(DocumentChunkModel, citation.chunk_id)
    document = db.get(DocumentModel, citation.document_id)
    return CitationResponse(
        id=citation.id,
        document_id=citation.document_id,
        knowledge_base_id=document.knowledge_base_id if document is not None else None,
        chunk_id=citation.chunk_id,
        snippet=citation.snippet,
        source_page=chunk.source_page if chunk is not None else None,
        source_filename=document.source_filename if document is not None else None,
    )


def message_response(message: MessageModel) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
    )
