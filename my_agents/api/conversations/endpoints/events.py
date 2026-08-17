"""Conversation run cancellation and event endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.interactions import delete_checkpoint_thread
from my_agents.api.conversations.run_events import append_run_event, event_response
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active
from my_agents.conversations.models import AgentEventModel, AgentEventType, AgentRunModel, RunStatus
from my_agents.conversations.schemas import AgentEventResponse, ConversationRunCancelResponse
from my_agents.persistence.database import get_database_session

router = APIRouter()


@router.post(
    "/{conversation_id}/runs/{run_id}/cancel",
    response_model=ConversationRunCancelResponse,
)
def cancel_run(
    conversation_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
) -> ConversationRunCancelResponse:
    """Request cooperative cancellation for the active run in an authorized conversation."""
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    run = db.get(AgentRunModel, run_id)
    if run is None or run.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    if run.status == RunStatus.WAITING_FOR_INPUT.value:
        run.status = RunStatus.CANCELLED.value
        run.interaction_id = None
        run.interaction_type = None
        run.interaction_payload_json = None
        run.interaction_expires_at = None
        append_run_event(
            db,
            run.id,
            AgentEventType.RUN_CANCELLED,
            {
                "run_id": run.id,
                "conversation_id": run.conversation_id,
                "status": RunStatus.CANCELLED.value,
                "partial_reply_persisted": False,
            },
            commit=False,
        )
        db.commit()
        delete_checkpoint_thread(graph_runner, run.id)
        db.refresh(run)
    elif run.status == RunStatus.RUNNING.value:
        run.status = RunStatus.CANCELLING.value
        append_run_event(
            db,
            run.id,
            AgentEventType.RUN_CANCEL_REQUESTED,
            {"run_id": run.id, "status": RunStatus.CANCELLING.value},
            commit=False,
        )
        db.commit()
        db.refresh(run)
    return ConversationRunCancelResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
    )


@router.get(
    "/{conversation_id}/runs/{run_id}/events",
    response_model=list[AgentEventResponse],
    response_model_exclude_none=True,
)
def list_run_events(
    conversation_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[AgentEventResponse]:
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    run = db.get(AgentRunModel, run_id)
    if run is None or run.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    events = db.scalars(
        select(AgentEventModel)
        .where(AgentEventModel.run_id == run_id)
        .order_by(AgentEventModel.sequence)
    ).all()
    return [event_response(event) for event in events]
