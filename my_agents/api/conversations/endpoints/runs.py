"""Conversation run endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.run_lifecycle import (
    assert_no_active_run,
    cleanup_stale_active_runs,
    complete_sync_conversation_run,
    fail_active_run,
    start_run,
)
from my_agents.api.conversations.serializers import run_detail_response, run_summary_response
from my_agents.api.conversations.transcripts import messages_for_conversation, store_user_message
from my_agents.api.document_workspace import get_document_workspace_provider
from my_agents.api.reasoning import resolve_reasoning_preferences
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_send_prompt
from my_agents.conversations.models import AgentRunModel, RunStatus
from my_agents.conversations.schemas import (
    AgentRunSummaryResponse,
    ConversationRunRequest,
    ConversationRunResponse,
)
from my_agents.document_workspace.provider import DocumentWorkspaceProvider
from my_agents.document_workspace.service import (
    assert_document_workspace_access,
    prepare_document_workspace_runtime,
)
from my_agents.knowledge.auth import resolve_conversation_knowledge_context
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
    document_workspace_provider: Annotated[
        DocumentWorkspaceProvider | None,
        Depends(get_document_workspace_provider),
    ],
) -> ConversationRunResponse:
    assert_guest_can_send_prompt(db, principal, settings)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    assert_no_active_run(db, conversation_id)
    selection_context = resolve_conversation_knowledge_context(
        db,
        principal=principal,
        requested_selection=request.knowledge_base_selection,
    )
    if request.attachment_ids:
        assert_document_workspace_access(settings=settings, principal=principal)
        if document_workspace_provider is None:
            raise RuntimeError("document workspace provider is unavailable")
    reasoning = resolve_reasoning_preferences(
        settings=settings,
        principal=principal,
        requested_mode=request.reasoning_mode,
        requested_effort=request.reasoning_effort,
        uses_document_workspace=bool(request.attachment_ids),
    )
    user_message = store_user_message(db, conversation_id, request.message)
    run = start_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        user_message_id=user_message.id,
        message_content_length=len(request.message.strip()),
        selection_context=selection_context,
        reasoning_mode=reasoning.mode,
        reasoning_effort=reasoning.effort,
    )
    document_workspace_runtime = None
    if request.attachment_ids:
        assert document_workspace_provider is not None
        try:
            document_workspace_runtime = prepare_document_workspace_runtime(
                db=db,
                provider=document_workspace_provider,
                settings=settings,
                principal=principal,
                conversation_id=conversation_id,
                run_id=run.id,
                attachment_ids=request.attachment_ids,
            )
        except Exception as exc:
            fail_active_run(
                db=db,
                run_id=run.id,
                conversation_id=conversation_id,
                error_type=type(exc).__name__,
            )
            raise
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
        document_workspace_runtime=document_workspace_runtime,
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
    cleanup_stale_active_runs(db, conversation_id)
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
