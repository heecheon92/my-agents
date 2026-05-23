"""Conversation run endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.run_lifecycle import (
    assert_no_active_run,
    complete_sync_conversation_run,
    start_run,
)
from my_agents.api.conversations.serializers import run_detail_response, run_summary_response
from my_agents.api.conversations.transcripts import messages_for_conversation, store_user_message
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_send_prompt
from my_agents.conversations.models import AgentRunModel, RunStatus
from my_agents.conversations.schemas import (
    AgentRunSummaryResponse,
    ConversationRunRequest,
    ConversationRunResponse,
)
from my_agents.knowledge.auth import resolve_knowledge_base_selection
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

router = APIRouter()


@router.post("/{conversation_id}/runs", response_model=ConversationRunResponse)
def run_conversation(
    conversation_id: str,
    request: ConversationRunRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConversationRunResponse:
    assert_guest_can_send_prompt(db, principal, settings)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    assert_no_active_run(db, conversation_id)
    selection_context = resolve_knowledge_base_selection(
        db,
        user_id=principal.user_id,
        mode=request.knowledge_base_selection.mode,
        knowledge_base_ids=request.knowledge_base_selection.knowledge_base_ids,
    )
    user_message = store_user_message(db, conversation_id, request.message)
    run = start_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        user_message_id=user_message.id,
        message_content_length=len(request.message.strip()),
        selection_context=selection_context,
    )
    messages = messages_for_conversation(db, conversation_id)
    return complete_sync_conversation_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        prompt=request.message,
        messages=messages,
        run=run,
        selection_context=selection_context,
        graph_runner=graph_runner,
    )


@router.get(
    "/{conversation_id}/runs",
    response_model=list[AgentRunSummaryResponse],
)
def list_runs(
    conversation_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[AgentRunSummaryResponse]:
    """Return frontend-safe run history for an authorized conversation."""
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    runs = db.scalars(
        select(AgentRunModel)
        .where(AgentRunModel.conversation_id == conversation_id)
        .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
    ).all()
    return [run_summary_response(run) for run in runs]


@router.get(
    "/{conversation_id}/runs/{run_id}",
    response_model=ConversationRunResponse,
)
def get_run(
    conversation_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ConversationRunResponse:
    """Return a refresh-safe completed run with reply and persisted citations."""
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    run = db.get(AgentRunModel, run_id)
    if run is None or run.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    if run.status != RunStatus.COMPLETED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is not completed")
    return run_detail_response(db, run)
