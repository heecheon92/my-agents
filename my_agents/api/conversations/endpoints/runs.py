"""Conversation run endpoints."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from langchain_core.messages import BaseMessage
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from my_agents.agents.general_assistant.graph import GRAPH_VERSION
from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.interactions import (
    delete_checkpoint_thread,
    interrupted_run_response,
)
from my_agents.api.conversations.run_events import append_run_event
from my_agents.api.conversations.run_lifecycle import (
    assert_no_active_run,
    cleanup_stale_active_runs,
    complete_resumed_conversation_run,
    complete_sync_conversation_run,
    fail_active_run,
    mark_run_cancelled,
    persist_failed_run,
    start_run,
)
from my_agents.api.conversations.serializers import (
    run_detail_response,
    run_knowledge_base_context,
    run_summary_response,
)
from my_agents.api.conversations.transcripts import messages_for_conversation, store_user_message
from my_agents.api.document_workspace import get_document_workspace_provider
from my_agents.api.errors import APIErrorCode, APIHTTPException
from my_agents.api.reasoning import resolve_reasoning_preferences
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_send_prompt
from my_agents.conversations.models import AgentEventType, AgentRunModel, RunStatus
from my_agents.conversations.schemas import (
    AgentRunSummaryResponse,
    ConversationRunInterruptedResponse,
    ConversationRunRequest,
    ConversationRunResult,
)
from my_agents.document_workspace.provider import DocumentWorkspaceProvider
from my_agents.document_workspace.service import (
    assert_document_workspace_access,
    prepare_document_workspace_runtime,
)
from my_agents.interactions.schemas import (
    INTERACTION_SCHEMA_VERSION,
    ConversationRunResumeRequest,
    DocumentSelectionOption,
    DocumentSelectionOptionsResponse,
)
from my_agents.knowledge.auth import (
    KnowledgeBaseSelectionContext,
    resolve_conversation_knowledge_context,
)
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.observability.metrics import record_langgraph_persistence_operation
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

router = APIRouter()


@router.post("/{conversation_id}/runs", response_model=ConversationRunResult)
def run_conversation(
    conversation_id: str,
    request: ConversationRunRequest,
    response: Response,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
    document_workspace_provider: Annotated[
        DocumentWorkspaceProvider | None,
        Depends(get_document_workspace_provider),
    ],
) -> ConversationRunResult:
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
    result = complete_sync_conversation_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        prompt=request.message,
        messages=messages,
        run=run,
        selection_context=selection_context,
        graph_runner=graph_runner,
        document_workspace_runtime=document_workspace_runtime,
        hitl_wait_seconds=settings.hitl_wait_seconds,
    )
    if isinstance(result, ConversationRunInterruptedResponse):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@dataclass(frozen=True)
class PreparedConversationRunResume:
    """Authorized, atomically claimed resume state shared by sync and SSE paths."""

    run: AgentRunModel
    messages: list[BaseMessage]
    selection_context: KnowledgeBaseSelectionContext
    selected_document_id: str
    resumed_event_payload: dict[str, object]


def prepare_conversation_run_resume(
    *,
    conversation_id: str,
    run_id: str,
    request: ConversationRunResumeRequest,
    principal: Principal,
    db: Session,
    graph_runner: GraphRunner,
) -> PreparedConversationRunResume:
    """Validate and atomically claim one waiting run before execution starts."""
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    run = db.get(AgentRunModel, run_id, populate_existing=True)
    if run is None or run.conversation_id != conversation_id or run.user_id != principal.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    if run.status != RunStatus.WAITING_FOR_INPUT.value:
        raise APIHTTPException(
            status_code=409,
            detail="run is not waiting for input",
            code=APIErrorCode.RUN_NOT_WAITING_FOR_INPUT,
        )
    if run.interaction_id != request.interaction_id:
        raise APIHTTPException(
            status_code=409,
            detail="run interaction does not match",
            code=APIErrorCode.RUN_INTERACTION_MISMATCH,
        )
    if run.interaction_expires_at is None or _as_utc(run.interaction_expires_at) <= datetime.now(
        UTC
    ):
        run.interaction_id = None
        run.interaction_type = None
        run.interaction_payload_json = None
        run.interaction_expires_at = None
        db.flush()
        mark_run_cancelled(db, run.id)
        delete_checkpoint_thread(graph_runner, run.id)
        raise APIHTTPException(
            status_code=409,
            detail="run interaction expired",
            code=APIErrorCode.RUN_INTERACTION_EXPIRED,
        )
    if run.graph_version != GRAPH_VERSION:
        persist_failed_run(
            db=db,
            run_id=run.id,
            conversation_id=conversation_id,
            error_type="GraphVersionIncompatible",
        )
        delete_checkpoint_thread(graph_runner, run.id)
        raise APIHTTPException(
            status_code=409,
            detail="run graph version is incompatible",
            code=APIErrorCode.RUN_GRAPH_VERSION_INCOMPATIBLE,
        )
    if getattr(graph_runner, "checkpointer", None) is None:
        raise APIHTTPException(
            status_code=503,
            detail="LangGraph persistence is unavailable",
            code=APIErrorCode.LANGGRAPH_PERSISTENCE_UNAVAILABLE,
        )
    selection_context = run_knowledge_base_context(run)
    if not RetrievalService(db).document_is_user_selectable(
        user_id=principal.user_id,
        document_id=request.document_id,
        knowledge_base_ids=selection_context.retrieval_knowledge_base_ids,
    ):
        raise APIHTTPException(
            status_code=409,
            detail="selected document is unavailable",
            code=APIErrorCode.RUN_SELECTED_DOCUMENT_UNAVAILABLE,
        )
    claimed = db.execute(
        update(AgentRunModel)
        .where(
            AgentRunModel.id == run.id,
            AgentRunModel.status == RunStatus.WAITING_FOR_INPUT.value,
            AgentRunModel.interaction_id == request.interaction_id,
        )
        .values(status=RunStatus.RUNNING.value, resumed_at=datetime.now(UTC))
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise APIHTTPException(
            status_code=409,
            detail="run is no longer waiting for input",
            code=APIErrorCode.RUN_NOT_WAITING_FOR_INPUT,
        )
    db.flush()
    db.refresh(run)
    resumed_event_payload: dict[str, object] = {
        "run_id": run.id,
        "status": RunStatus.RUNNING.value,
        "interaction_id": request.interaction_id,
        "interaction_schema_version": request.schema_version,
        "interaction_type": "document_selection",
    }
    append_run_event(
        db,
        run.id,
        AgentEventType.RUN_RESUMED,
        resumed_event_payload,
        commit=False,
    )
    db.commit()
    record_langgraph_persistence_operation(operation="resume", outcome="accepted")
    return PreparedConversationRunResume(
        run=run,
        messages=messages_for_conversation(db, conversation_id),
        selection_context=selection_context,
        selected_document_id=request.document_id,
        resumed_event_payload=resumed_event_payload,
    )


@router.post(
    "/{conversation_id}/runs/{run_id}/resume",
    response_model=ConversationRunResult,
)
def resume_conversation_run(
    conversation_id: str,
    run_id: str,
    request: ConversationRunResumeRequest,
    response: Response,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConversationRunResult:
    """Resume one authorized document-selection checkpoint without storing a new prompt."""
    prepared = prepare_conversation_run_resume(
        conversation_id=conversation_id,
        run_id=run_id,
        request=request,
        principal=principal,
        db=db,
        graph_runner=graph_runner,
    )
    result = complete_resumed_conversation_run(
        db=db,
        run=prepared.run,
        messages=prepared.messages,
        selection_context=prepared.selection_context,
        selected_document_id=prepared.selected_document_id,
        graph_runner=graph_runner,
        hitl_wait_seconds=settings.hitl_wait_seconds,
    )
    if isinstance(result, ConversationRunInterruptedResponse):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@router.get(
    "/{conversation_id}/runs/{run_id}/interactions/{interaction_id}/options",
    response_model=DocumentSelectionOptionsResponse,
)
def list_document_selection_options(
    conversation_id: str,
    run_id: str,
    interaction_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    cursor: Annotated[str | None, Query()] = None,
) -> DocumentSelectionOptionsResponse:
    """Page through the live authorized source scope for one waiting run."""
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    run = db.get(AgentRunModel, run_id)
    if run is None or run.conversation_id != conversation_id or run.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != RunStatus.WAITING_FOR_INPUT.value or run.interaction_id != interaction_id:
        raise APIHTTPException(
            status_code=409,
            detail="run interaction does not match",
            code=APIErrorCode.RUN_INTERACTION_MISMATCH,
        )
    try:
        offset = max(int(cursor or "0"), 0)
    except ValueError as exc:
        raise APIHTTPException(
            status_code=400,
            detail="interaction options cursor is invalid",
            code=APIErrorCode.INVALID_REQUEST,
        ) from exc
    options, total = RetrievalService(db).authorized_document_options(
        user_id=principal.user_id,
        knowledge_base_ids=run_knowledge_base_context(run).retrieval_knowledge_base_ids,
        limit=50,
        offset=offset,
    )
    next_offset = offset + len(options)
    return DocumentSelectionOptionsResponse(
        schema_version=INTERACTION_SCHEMA_VERSION,
        interaction_id=interaction_id,
        type="document_selection",
        option_count=total,
        options=[DocumentSelectionOption(**option.__dict__) for option in options],
        next_cursor=str(next_offset) if next_offset < total else None,
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
    response_model=ConversationRunResult,
)
def get_run(
    conversation_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> ConversationRunResult:
    """Return a refresh-safe completed run with reply and persisted citations."""
    assert_guest_access_active(db, principal)
    get_authorized_conversation(db, conversation_id, principal.user_id)
    run = db.get(AgentRunModel, run_id)
    if run is None or run.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    if run.status == RunStatus.WAITING_FOR_INPUT.value:
        return interrupted_run_response(run)
    if run.status != RunStatus.COMPLETED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is not completed")
    return run_detail_response(db, run)
