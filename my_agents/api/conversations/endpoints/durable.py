"""Durable admission and read-only recoverable SSE subscriptions."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, get_args

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from my_agents.api.assistant import GraphRunner, get_graph_runner
from my_agents.api.conversations.auth import get_authorized_conversation
from my_agents.api.conversations.endpoints.replay import replay_context_for_request
from my_agents.api.conversations.endpoints.runs import get_run, prepare_conversation_run_resume
from my_agents.api.conversations.run_lifecycle import admit_run, assert_no_active_run
from my_agents.api.conversations.sse_contract import conversation_sse_responses
from my_agents.api.document_workspace import get_document_workspace_provider
from my_agents.api.errors import APIErrorCode, APIHTTPException
from my_agents.api.reasoning import resolve_reasoning_preferences
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active, assert_guest_can_send_prompt
from my_agents.conversations.durable_models import DurableRunJob, DurableRunOutput
from my_agents.conversations.durable_runtime import reap_lost_jobs, repair_terminal, worker_ready
from my_agents.conversations.durable_schemas import (
    DurableAdmission,
    DurableCancellation,
    DurableFailure,
    DurableOutput,
    DurableReplayRequest,
    DurableResumeRequest,
    DurableRunRequest,
)
from my_agents.conversations.durable_store import (
    enqueue_job,
    find_job,
    get_output,
    latest_terminal,
    list_frames,
    request_fingerprint,
)
from my_agents.conversations.models import AgentRunModel
from my_agents.conversations.schemas import (
    AgentEventResponse,
    ConversationRunInterruptedResponse,
    ConversationRunResponse,
)
from my_agents.document_workspace.provider import DocumentWorkspaceProvider
from my_agents.document_workspace.service import assert_document_workspace_access
from my_agents.knowledge.auth import resolve_conversation_knowledge_context
from my_agents.persistence.database import _sessionmaker_for_url, get_database_session
from my_agents.settings import Settings, get_settings

router = APIRouter()
PrincipalDep = Annotated[Principal, Depends(get_current_principal)]
DBDep = Annotated[Session, Depends(get_database_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
GraphDep = Annotated[GraphRunner, Depends(get_graph_runner)]
ProviderDep = Annotated[DocumentWorkspaceProvider | None, Depends(get_document_workspace_provider)]


def receipt(db: Session, job: DurableRunJob) -> DurableAdmission:
    run = db.get(AgentRunModel, job.run_id, populate_existing=True)
    if run is None:
        raise HTTPException(404, "run not found")
    return DurableAdmission(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        client_request_id=job.client_request_id,
    )


def existing(
    db: Session, principal: Principal, kind: str, key: str, payload: dict
) -> DurableAdmission | None:
    job = find_job(db, principal.user_id, kind, key)
    if job is None:
        return None
    if job.request_fingerprint != request_fingerprint(payload):
        raise APIHTTPException(
            status_code=409,
            detail="This request ID was used for different input.",
            code=APIErrorCode.RUN_REQUEST_ID_CONFLICT,
        )
    return receipt(db, job)


def require_worker(settings: Settings) -> None:
    if not worker_ready(settings):
        raise APIHTTPException(
            status_code=503,
            detail="Durable answer execution is not available.",
            code=APIErrorCode.DURABLE_RUNS_UNAVAILABLE,
        )


def queued(
    db: Session, run: AgentRunModel, principal: Principal, kind: str, key: str, payload: dict
) -> DurableAdmission:
    run.status = "queued"
    if kind == "resume":
        output = db.get(DurableRunOutput, run.id)
        projection = json.loads(output.snapshot_json)
        projection["interaction"] = projection["error"] = None
        output.snapshot_json = json.dumps(projection)
        output.terminal_event = output.terminal_data_json = output.terminal_cursor = None
    job = enqueue_job(
        db,
        run_id=run.id,
        user_id=principal.user_id,
        kind=kind,
        client_request_id=key,
        request_json=payload,
    )
    db.commit()
    return receipt(db, job)


@router.post("/{conversation_id}/runs/async", status_code=202, response_model=DurableAdmission)
def admit_durable(
    conversation_id: str,
    request: DurableRunRequest,
    principal: PrincipalDep,
    db: DBDep,
    settings: SettingsDep,
    provider: ProviderDep,
) -> DurableAdmission:
    get_authorized_conversation(db, conversation_id, principal.user_id)
    assert_guest_access_active(db, principal)
    payload = {"conversation_id": conversation_id, "request": request.model_dump(mode="json")}
    key = str(request.client_request_id)
    found = existing(db, principal, "run", key, payload)
    if found:
        return found
    require_worker(settings)
    try:
        if principal.is_guest:
            from my_agents.auth.models import UserModel

            db.execute(
                update(UserModel)
                .where(UserModel.id == principal.user_id)
                .values(approval_status=UserModel.approval_status)
            )
        assert_guest_can_send_prompt(db, principal, settings)
        assert_no_active_run(db, conversation_id)
        selection = resolve_conversation_knowledge_context(
            db, principal=principal, requested_selection=request.knowledge_base_selection
        )
        if request.attachment_ids:
            assert_document_workspace_access(settings=settings, principal=principal)
            if provider is None:
                raise HTTPException(503, "Document workspace is unavailable.")
        reasoning = resolve_reasoning_preferences(
            db=db,
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
            selection_context=selection,
            reasoning_mode=reasoning.mode,
            reasoning_effort=reasoning.effort,
            assistant_model=reasoning.model,
            attachment_ids=request.attachment_ids,
            client_request_id=key,
            requested_reasoning_mode=request.reasoning_mode,
            requested_reasoning_effort=request.reasoning_effort,
            commit=False,
        )
        return queued(db, admitted.run, principal, "run", key, payload)
    except IntegrityError, HTTPException:
        db.rollback()
        found = existing(db, principal, "run", key, payload)
        if found:
            return found
        raise


@router.post(
    "/{conversation_id}/runs/{run_id}/resume/async",
    status_code=202,
    response_model=DurableAdmission,
)
def resume_durable(
    conversation_id: str,
    run_id: str,
    request: DurableResumeRequest,
    principal: PrincipalDep,
    db: DBDep,
    settings: SettingsDep,
    graph_runner: GraphDep,
) -> DurableAdmission:
    authorize_output(db, conversation_id, run_id, principal)
    payload = {
        "conversation_id": conversation_id,
        "run_id": run_id,
        "request": request.model_dump(mode="json"),
    }
    key = str(request.client_request_id)
    found = existing(db, principal, "resume", key, payload)
    if found:
        return found
    require_worker(settings)
    try:
        # Fence the previous paused phase before claiming its continuation. Its
        # worker may still be finishing delivery, but must never mutate the new phase.
        prior_jobs = db.scalars(
            select(DurableRunJob)
            .where(DurableRunJob.run_id == run_id, DurableRunJob.status == "running")
            .with_for_update()
        ).all()
        run = db.get(AgentRunModel, run_id, populate_existing=True)
        if run.status == "waiting_for_input":
            for prior_job in prior_jobs:
                prior_job.status = "done"
                prior_job.lease_token = None
                prior_job.lease_expires_at = None
            db.flush()
        prepared = prepare_conversation_run_resume(
            conversation_id=conversation_id,
            run_id=run_id,
            request=request.answer,
            principal=principal,
            db=db,
            graph_runner=graph_runner,
            commit=False,
            durable=True,
        )
        # The validated semantic request is sufficient to reconstruct resume in the worker.
        return queued(db, prepared.run, principal, "resume", key, payload)
    except IntegrityError, HTTPException:
        db.rollback()
        found = existing(db, principal, "resume", key, payload)
        if found:
            return found
        raise


@router.post(
    "/{conversation_id}/messages/{message_id}/replay/async",
    status_code=202,
    response_model=DurableAdmission,
)
def replay_durable(
    conversation_id: str,
    message_id: str,
    request: DurableReplayRequest,
    principal: PrincipalDep,
    db: DBDep,
    settings: SettingsDep,
) -> DurableAdmission:
    get_authorized_conversation(db, conversation_id, principal.user_id)
    assert_guest_access_active(db, principal)
    payload = {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "request": request.model_dump(mode="json"),
    }
    key = str(request.client_request_id)
    found = existing(db, principal, "replay", key, payload)
    if found:
        return found
    require_worker(settings)
    try:
        context = replay_context_for_request(
            db,
            conversation_id=conversation_id,
            message_id=message_id,
            principal=principal,
            settings=settings,
            request=request.request,
        )
        run = admit_run(
            db=db,
            conversation_id=conversation_id,
            user_id=principal.user_id,
            existing_user_message=context.preceding_message,
            selection_context=context.selection_context,
            reasoning_mode=context.reasoning_mode,
            reasoning_effort=context.reasoning_effort,
            assistant_model=context.assistant_model,
            commit=False,
        ).run
        return queued(db, run, principal, "replay", key, payload)
    except IntegrityError, HTTPException:
        db.rollback()
        found = existing(db, principal, "replay", key, payload)
        if found:
            return found
        raise


def authorize_output(db: Session, cid: str, rid: str, principal: Principal) -> AgentRunModel:
    assert_guest_access_active(db, principal)
    from datetime import UTC, datetime

    from my_agents.auth.models import SessionModel, UserModel
    from my_agents.conversations.durable_runtime import utc

    user = db.get(UserModel, principal.user_id, populate_existing=True)
    session = db.get(SessionModel, principal.session_id, populate_existing=True)
    if (
        user is None
        or user.approval_status != "approved"
        or session is None
        or session.user_id != principal.user_id
        or session.revoked_at is not None
        or (session.expires_at is not None and utc(session.expires_at) <= datetime.now(UTC))
    ):
        raise HTTPException(401, "Authentication required.")
    get_authorized_conversation(db, cid, principal.user_id)
    run = db.get(AgentRunModel, rid, populate_existing=True)
    if (
        run is None
        or run.conversation_id != cid
        or run.user_id != principal.user_id
        or db.get(DurableRunOutput, rid) is None
    ):
        raise HTTPException(404, "run not found")
    return run


@router.get("/{conversation_id}/runs/{run_id}/output", response_model=DurableOutput)
def output_snapshot(
    conversation_id: str, run_id: str, principal: PrincipalDep, db: DBDep, settings: SettingsDep
) -> DurableOutput:
    authorize_output(db, conversation_id, run_id, principal)
    db.rollback()
    reap_lost_jobs(settings)
    run = authorize_output(db, conversation_id, run_id, principal)
    if repair_terminal(db, run):
        db.commit()
    # A repaired terminal commit may race a new resume phase. Reacquire the run
    # before reading its snapshot and refreshing permission-filtered interaction options.
    db.execute(
        update(AgentRunModel).where(AgentRunModel.id == run_id).values(status=AgentRunModel.status)
    )
    db.refresh(run)
    # One SELECT joins status with the materialized text/cursor projection.
    row = db.execute(
        select(
            AgentRunModel.status,
            AgentRunModel.client_request_id,
            DurableRunOutput.cursor,
            DurableRunOutput.snapshot_json,
        )
        .join(DurableRunOutput, DurableRunOutput.run_id == AgentRunModel.id)
        .where(AgentRunModel.id == run_id)
    ).one()
    projection = json.loads(row.snapshot_json)
    if row.status == "waiting_for_input":
        current = get_run(conversation_id, run_id, principal, db)
        projection["interaction"] = current.interaction.model_dump(mode="json")
    return DurableOutput(
        run_id=run_id,
        conversation_id=conversation_id,
        client_request_id=row.client_request_id
        or receipt(
            db,
            db.scalar(
                select(DurableRunJob)
                .where(DurableRunJob.run_id == run_id)
                .order_by(DurableRunJob.created_at)
                .limit(1)
            ),
        ).client_request_id,
        status=row.status,
        cursor=row.cursor,
        **projection,
    )


def cursor_value(after: str | None, last_id: str | None) -> int:
    values = [v for v in (after, last_id) if v is not None]
    if any(not v.isascii() or not v.isdigit() or len(v) > 18 for v in values) or (
        len(values) == 2 and int(values[0]) != int(values[1])
    ):
        raise APIHTTPException(
            status_code=400,
            detail="Invalid stream cursor.",
            code=APIErrorCode.INVALID_STREAM_CURSOR,
        )
    return int(values[0]) if values else 0


def validate_cursor(db: Session, run_id: str, cursor: int) -> None:
    output = get_output(db, run_id)
    if cursor > output.cursor:
        raise APIHTTPException(
            status_code=400,
            detail="Stream cursor is ahead of saved output.",
            code=APIErrorCode.INVALID_STREAM_CURSOR,
        )
    if cursor < output.min_cursor:
        raise APIHTTPException(
            status_code=410,
            detail="Fetch a fresh output snapshot to continue.",
            code=APIErrorCode.RUN_OUTPUT_CURSOR_EXPIRED,
        )


def durable_sse_responses() -> dict:
    responses = conversation_sse_responses(
        "Subscribe to saved output without owning execution. "
        "Reconnect using after or Last-Event-ID."
    )
    events = responses[200]["content"]["text/event-stream"]["x-sse-events"]
    for envelope in get_args(get_args(AgentEventResponse.__value__)[0]):
        for name in get_args(envelope.model_fields["event_type"].annotation):
            if name in {"run_failed", "run_cancelled", "run_interrupted"}:
                continue
            model = envelope.model_fields["payload"].annotation
            schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
            schema.pop("$defs", None)
            events[name] = {"schema": schema}
    for name, model in [
        ("run_completed", ConversationRunResponse),
        ("run_interrupted", ConversationRunInterruptedResponse),
        ("run_failed", DurableFailure),
        ("run_cancelled", DurableCancellation),
    ]:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schema.pop("$defs", None)
        events[name] = {"schema": schema}
    events["answer_delta"] = {
        "schema": {
            "type": "object",
            "required": ["delta", "sequence"],
            "properties": {
                "delta": {"type": "string"},
                "sequence": {"type": "integer", "minimum": 1},
            },
        }
    }
    return responses


@router.get("/{conversation_id}/runs/{run_id}/stream", responses=durable_sse_responses())
def subscribe_output(
    conversation_id: str,
    run_id: str,
    request: Request,
    principal: PrincipalDep,
    db: DBDep,
    settings: SettingsDep,
    after: Annotated[str | None, Query()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    authorize_output(db, conversation_id, run_id, principal)
    cursor = cursor_value(after, last_event_id)
    validate_cursor(db, run_id, cursor)
    db.rollback()  # Never hold the request session/transaction for the subscriber lifetime.

    async def listen():
        position = cursor
        heartbeat = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            await asyncio.to_thread(reap_lost_jobs, settings)

            def read(position=position):
                with _sessionmaker_for_url(settings.database_url)() as session:
                    run = authorize_output(session, conversation_id, run_id, principal)
                    if repair_terminal(session, run):
                        session.commit()
                        run = authorize_output(session, conversation_id, run_id, principal)
                    validate_cursor(session, run_id, position)
                    frames = [
                        (f.cursor, f.event, f.data_json)
                        for f in list_frames(session, run_id, position)
                    ]
                    terminal = (
                        latest_terminal(session, run_id)
                        if run.status in {"completed", "failed", "cancelled", "waiting_for_input"}
                        else None
                    )
                    if terminal and terminal.event == "run_interrupted":
                        safe_data = json.dumps(
                            get_run(conversation_id, run_id, principal, session).model_dump(
                                mode="json"
                            )
                        )
                        frames = [
                            (
                                seq,
                                name,
                                safe_data
                                if name == "run_interrupted" and seq == terminal.cursor
                                else data,
                            )
                            for seq, name, data in frames
                        ]
                        terminal.data_json = safe_data
                    return frames, (
                        terminal.cursor,
                        terminal.event,
                        terminal.data_json,
                    ) if terminal else None

            try:
                frames, terminal = await asyncio.to_thread(read)
            except HTTPException:
                # A fresh subscription/snapshot surfaces the typed HTTP status; never leak data.
                return
            for seq, name, data in frames:
                position = seq
                if name in {"run_completed", "run_failed", "run_cancelled", "run_interrupted"} and (
                    terminal is None or terminal[0] != seq
                ):
                    continue  # Old HITL phase boundary is not the current close signal.
                yield f"id: {seq}\nevent: {name}\ndata: {data}\n\n"
                if name in {"run_completed", "run_failed", "run_cancelled", "run_interrupted"}:
                    return
            if terminal and terminal[0] <= position:
                seq, name, data = terminal
                yield f"id: {seq}\nevent: {name}\ndata: {data}\n\n"
                return
            if asyncio.get_running_loop().time() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = asyncio.get_running_loop().time()
            await asyncio.sleep(settings.durable_run_poll_seconds)

    return StreamingResponse(
        listen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
