"""Assistant-message replay endpoint."""

import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import (
    get_authorized_conversation,
    require_conversation_source_membership,
)
from my_agents.api.conversations.run_lifecycle import (
    assert_no_active_run,
    complete_sync_conversation_run,
    start_run,
)
from my_agents.api.conversations.serializers import run_knowledge_base_selection
from my_agents.api.conversations.transcripts import (
    base_messages_from_persisted,
    persisted_messages_for_conversation,
    preceding_user_message,
    prune_conversation_from_message,
    run_for_assistant_message,
)
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_can_send_prompt
from my_agents.conversations.models import AgentRunModel, MessageModel, MessageRole
from my_agents.conversations.schemas import (
    ConversationReplayRequest,
    ConversationRunResponse,
    ConversationRunWarning,
)
from my_agents.knowledge.auth import resolve_conversation_knowledge_context
from my_agents.knowledge.models import DocumentModel
from my_agents.knowledge.schemas import KnowledgeBaseSelection
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

router = APIRouter()


@router.post(
    "/{conversation_id}/messages/{message_id}/replay",
    response_model=ConversationRunResponse,
)
def replay_assistant_message(
    conversation_id: str,
    message_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Annotated[ConversationReplayRequest, Body(default_factory=ConversationReplayRequest)],
) -> ConversationRunResponse:
    """Regenerate an existing assistant message from the transcript prefix before it.

    V1 keeps the transcript linear: the target assistant message and all later
    messages, runs, events, and citations in the conversation are pruned before a
    fresh run is created from the preceding user turn and earlier history.
    """
    assert_guest_can_send_prompt(db, principal, settings)
    conversation = get_authorized_conversation(db, conversation_id, principal.user_id)
    require_conversation_source_membership(db, conversation, principal.user_id)
    assert_no_active_run(db, conversation_id)
    target_message = db.get(MessageModel, message_id)
    if target_message is None or target_message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message not found")
    if target_message.role != MessageRole.ASSISTANT.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="message is not an assistant message",
        )

    persisted_messages = persisted_messages_for_conversation(db, conversation_id)
    try:
        target_index = next(
            index for index, message in enumerate(persisted_messages) if message.id == message_id
        )
    except StopIteration as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="message not found"
        ) from exc
    prefix_messages = persisted_messages[:target_index]
    preceding_message = preceding_user_message(prefix_messages)
    if preceding_message is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="assistant message has no preceding user prompt",
        )

    original_run = run_for_assistant_message(db, conversation_id, message_id)
    replay_warnings = source_warnings_for_replay(db, original_run)
    requested_selection = (
        run_knowledge_base_selection(original_run)
        if original_run is not None
        else request.knowledge_base_selection or KnowledgeBaseSelection()
    )
    optional_personal_knowledge_base_ids = (
        _json_string_list(original_run.optional_personal_knowledge_base_ids_json)
        if original_run is not None
        else request.optional_personal_knowledge_base_ids
    )
    selection_context = resolve_conversation_knowledge_context(
        db,
        user_id=principal.user_id,
        conversation=conversation,
        requested_selection=requested_selection,
        optional_personal_knowledge_base_ids=optional_personal_knowledge_base_ids,
    )
    prune_conversation_from_message(
        db,
        conversation_id=conversation_id,
        target_message=target_message,
        removed_messages=persisted_messages[target_index:],
        original_run=original_run,
    )
    run = start_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        user_message_id=preceding_message.id,
        message_content_length=len(preceding_message.content.strip()),
        selection_context=selection_context,
    )
    messages = base_messages_from_persisted(prefix_messages)
    return complete_sync_conversation_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        prompt=preceding_message.content,
        messages=messages,
        run=run,
        selection_context=selection_context,
        graph_runner=graph_runner,
        warnings=replay_warnings,
    )


def source_warnings_for_replay(
    db: Session, original_run: AgentRunModel | None
) -> list[ConversationRunWarning]:
    if original_run is None or original_run.retrieval_source_snapshot_json is None:
        return []
    sources = _retrieval_source_snapshot(original_run.retrieval_source_snapshot_json)
    if not sources:
        return []
    document_ids = [source["document_id"] for source in sources if source.get("document_id")]
    if not document_ids:
        return []
    existing_ids = set(
        db.scalars(select(DocumentModel.id).where(DocumentModel.id.in_(document_ids))).all()
    )
    missing_sources = [
        source for source in sources if source.get("document_id") not in existing_ids
    ]
    if not missing_sources:
        return []
    missing_document_ids = sorted(
        {source["document_id"] for source in missing_sources if source.get("document_id")}
    )
    missing_source_filenames = sorted(
        {source["source_filename"] for source in missing_sources if source.get("source_filename")}
    )
    return [
        ConversationRunWarning(
            code="regeneration_sources_unavailable",
            message=(
                "Some sources used in the original answer are no longer available. "
                "This regeneration used currently available knowledge only."
            ),
            missing_document_ids=missing_document_ids,
            missing_source_filenames=missing_source_filenames,
        )
    ]


def _retrieval_source_snapshot(raw_json: str) -> list[dict[str, str | None]]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    sources: list[dict[str, str | None]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        document_id = item.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            continue
        source_filename = item.get("source_filename")
        sources.append(
            {
                "document_id": document_id,
                "source_filename": source_filename if isinstance(source_filename, str) else None,
            }
        )
    return sources


def _json_string_list(raw_json: str | None) -> list[str]:
    try:
        parsed = json.loads(raw_json or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []
