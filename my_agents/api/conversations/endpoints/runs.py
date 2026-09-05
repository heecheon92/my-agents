"""Conversation run endpoints."""

import json
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
    admit_run,
    assert_no_active_run,
    cleanup_stale_active_runs,
    complete_resumed_conversation_run,
    complete_sync_conversation_run,
    fail_active_run,
    mark_run_cancelled,
    persist_failed_run,
)
from my_agents.api.conversations.serializers import (
    run_detail_response,
    run_knowledge_base_context,
    run_summary_response,
)
from my_agents.api.conversations.transcripts import messages_for_conversation
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
    ConversationRunRefineRequestV2,
    ConversationRunResumeRequest,
    ConversationRunResumeRequestType,
    ConversationRunSelectRequestV2,
    DocumentSelectionOption,
    DocumentSelectionOptionsResponse,
    DocumentSelectionOptionsResponseV2,
    DocumentSelectionOptionsResult,
    DocumentSelectionOptionV2,
    PendingDocumentSelection,
    PendingDocumentSelectionV2,
    pending_interaction_adapter,
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
    admitted = admit_run(
        db=db,
        conversation_id=conversation_id,
        user_id=principal.user_id,
        message=request.message,
        selection_context=selection_context,
        reasoning_mode=reasoning.mode,
        reasoning_effort=reasoning.effort,
    )
    run = admitted.run
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
    resume_value: dict[str, object]
    resumed_event_payload: dict[str, object]


def prepare_conversation_run_resume(
    *,
    conversation_id: str,
    run_id: str,
    request: ConversationRunResumeRequestType,
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
    try:
        stored_interaction = pending_interaction_adapter.validate_python(
            json.loads(run.interaction_payload_json or "{}")
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise APIHTTPException(
            status_code=409,
            detail="stored run interaction is invalid",
            code=APIErrorCode.RUN_INTERACTION_MISMATCH,
        ) from exc
    if (
        stored_interaction.schema_version != request.schema_version
        or stored_interaction.type != request.type
    ):
        raise APIHTTPException(
            status_code=409,
            detail="run interaction version does not match",
            code=APIErrorCode.RUN_INTERACTION_MISMATCH,
        )
    retrieval_service = RetrievalService(db)
    if isinstance(request, (ConversationRunResumeRequest, ConversationRunSelectRequestV2)):
        document_id = request.document_id
        if isinstance(stored_interaction, PendingDocumentSelectionV2):
            shortlist_ids = {option.document_id for option in stored_interaction.options}
            if document_id not in shortlist_ids and not stored_interaction.browse.allowed:
                raise APIHTTPException(
                    status_code=409,
                    detail="selected document is outside the offered candidates",
                    code=APIErrorCode.RUN_INTERACTION_SELECTION_UNAVAILABLE,
                )
        if not retrieval_service.document_is_user_selectable(
            user_id=principal.user_id,
            document_id=document_id,
            knowledge_base_ids=selection_context.retrieval_knowledge_base_ids,
        ):
            raise APIHTTPException(
                status_code=409,
                detail="selected document is unavailable",
                code=APIErrorCode.RUN_SELECTED_DOCUMENT_UNAVAILABLE,
            )
        resume_value: dict[str, object] = (
            {"document_id": document_id}
            if isinstance(request, ConversationRunResumeRequest)
            else {"kind": "select", "document_id": document_id}
        )
    elif isinstance(request, ConversationRunRefineRequestV2):
        if not isinstance(stored_interaction, PendingDocumentSelectionV2):
            raise APIHTTPException(
                status_code=409,
                detail="run interaction does not support refinement",
                code=APIErrorCode.RUN_INTERACTION_MISMATCH,
            )
        if not stored_interaction.refinement.allowed:
            raise APIHTTPException(
                status_code=409,
                detail="document refinement attempts are exhausted",
                code=APIErrorCode.RUN_INTERACTION_REFINEMENT_EXHAUSTED,
            )
        resume_value = {"kind": "refine", "text": request.text}
    else:  # pragma: no cover - closed request union
        raise AssertionError("unsupported conversation run resume request")
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
        resume_value=resume_value,
        resumed_event_payload=resumed_event_payload,
    )


@router.post(
    "/{conversation_id}/runs/{run_id}/resume",
    response_model=ConversationRunResult,
)
def resume_conversation_run(
    conversation_id: str,
    run_id: str,
    request: ConversationRunResumeRequestType,
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
        resume_value=prepared.resume_value,
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
    response_model=DocumentSelectionOptionsResult,
)
def list_document_selection_options(
    conversation_id: str,
    run_id: str,
    interaction_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    cursor: Annotated[str | None, Query()] = None,
) -> DocumentSelectionOptionsResult:
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
    if run.interaction_expires_at is None or _as_utc(run.interaction_expires_at) <= datetime.now(
        UTC
    ):
        raise APIHTTPException(
            status_code=409,
            detail="run interaction expired",
            code=APIErrorCode.RUN_INTERACTION_EXPIRED,
        )
    try:
        stored_interaction = pending_interaction_adapter.validate_python(
            json.loads(run.interaction_payload_json or "{}")
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise APIHTTPException(
            status_code=409,
            detail="stored run interaction is invalid",
            code=APIErrorCode.RUN_INTERACTION_MISMATCH,
        ) from exc
    if (
        isinstance(stored_interaction, PendingDocumentSelectionV2)
        and not stored_interaction.browse.allowed
    ):
        raise APIHTTPException(
            status_code=409,
            detail="broad document browsing is not available",
            code=APIErrorCode.RUN_INTERACTION_REFINEMENT_EXHAUSTED,
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
    if isinstance(stored_interaction, PendingDocumentSelection):
        return DocumentSelectionOptionsResponse(
            schema_version=1,
            interaction_id=interaction_id,
            type="document_selection",
            option_count=total,
            options=[DocumentSelectionOption(**option.__dict__) for option in options],
            next_cursor=str(next_offset) if next_offset < total else None,
        )
    return DocumentSelectionOptionsResponseV2(
        schema_version=INTERACTION_SCHEMA_VERSION,
        interaction_id=interaction_id,
        type="document_selection",
        mode="broad",
        option_count=total,
        library_count=total,
        options=[DocumentSelectionOptionV2(**option.__dict__) for option in options],
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
        if run.interaction_expires_at is None or _as_utc(
            run.interaction_expires_at
        ) <= datetime.now(UTC):
            raise APIHTTPException(
                status_code=409,
                detail="run interaction expired",
                code=APIErrorCode.RUN_INTERACTION_EXPIRED,
            )
        response = interrupted_run_response(run)
        interaction = response.interaction
        service = RetrievalService(db)
        selection_context = run_knowledge_base_context(run)
        if isinstance(interaction, PendingDocumentSelection):
            current_options, library_count = service.authorized_document_options(
                user_id=principal.user_id,
                knowledge_base_ids=selection_context.retrieval_knowledge_base_ids,
                limit=50,
                offset=0,
            )
            refreshed_interaction = interaction.model_copy(
                update={
                    "option_count": library_count,
                    "options": [
                        DocumentSelectionOption(**option.__dict__) for option in current_options
                    ],
                    "next_cursor": "50" if library_count > 50 else None,
                }
            )
            return ConversationRunInterruptedResponse(
                run_id=response.run_id,
                conversation_id=response.conversation_id,
                interaction=refreshed_interaction,
            )
        current_options = service.authorized_document_options_by_ids(
            user_id=principal.user_id,
            document_ids=[option.document_id for option in interaction.options],
            knowledge_base_ids=selection_context.retrieval_knowledge_base_ids,
        )
        current_by_id = {option.document_id: option for option in current_options}
        options = [
            DocumentSelectionOptionV2(
                **current_by_id[option.document_id].__dict__,
                match_confidence=option.match_confidence,
                match_reason_code=option.match_reason_code,
            )
            for option in interaction.options
            if option.document_id in current_by_id
        ]
        _first_page, library_count = service.authorized_document_options(
            user_id=principal.user_id,
            knowledge_base_ids=selection_context.retrieval_knowledge_base_ids,
            limit=1,
            offset=0,
        )
        refreshed_interaction = interaction.model_copy(
            update={
                "reason_code": (
                    interaction.reason_code if options else "unresolved_document_reference"
                ),
                "option_count": len(options),
                "library_count": library_count,
                "options": options,
            }
        )
        return ConversationRunInterruptedResponse(
            run_id=response.run_id,
            conversation_id=response.conversation_id,
            interaction=refreshed_interaction,
        )
    if run.status != RunStatus.COMPLETED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is not completed")
    return run_detail_response(db, run)
