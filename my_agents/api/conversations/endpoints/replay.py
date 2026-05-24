"""Assistant-message replay endpoint."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
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
from my_agents.api.conversations.serializers import (
    run_knowledge_base_context,
    run_knowledge_base_selection,
)
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
from my_agents.conversations.models import MessageModel, MessageRole
from my_agents.conversations.schemas import (
    ConversationReplayRequest,
    ConversationRunResponse,
)
from my_agents.knowledge.auth import resolve_conversation_knowledge_context
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
    if original_run is not None and original_run.resolved_knowledge_base_ids_json is not None:
        selection_context = run_knowledge_base_context(original_run)
    else:
        requested_selection = (
            run_knowledge_base_selection(original_run)
            if original_run is not None
            else request.knowledge_base_selection or KnowledgeBaseSelection()
        )
        selection_context = resolve_conversation_knowledge_context(
            db,
            user_id=principal.user_id,
            conversation=conversation,
            requested_selection=requested_selection,
            optional_personal_knowledge_base_ids=request.optional_personal_knowledge_base_ids,
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
    )
