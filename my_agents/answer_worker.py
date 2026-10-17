"""Independent worker for durable conversation execution; never owned by an HTTP listener."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace

from fastapi import HTTPException
from sqlalchemy import delete, select, update

from my_agents.api.conversations.endpoints.replay import (
    replay_context_for_request,
    replay_conversation_run_events,
)
from my_agents.api.conversations.endpoints.runs import PreparedConversationRunResume
from my_agents.api.conversations.endpoints.stream import (
    conversation_run_events,
    resumed_conversation_run_events,
)
from my_agents.api.conversations.run_events import sse_event
from my_agents.api.conversations.run_lifecycle import AdmittedRun
from my_agents.api.conversations.serializers import run_knowledge_base_selection
from my_agents.api.conversations.transcripts import messages_for_conversation
from my_agents.auth.contracts import Principal
from my_agents.auth.guest_limits import assert_guest_access_active
from my_agents.auth.models import UserModel
from my_agents.conversations.durable_models import DurableRunJob, DurableWorkerHeartbeat
from my_agents.conversations.durable_runtime import (
    ExecutionStopped,
    emit,
    fail_job,
    install_fence,
    reap_lost_jobs,
    repair_terminal,
)
from my_agents.conversations.durable_schemas import (
    DurableCancellation,
    DurableFailure,
    DurableReplayRequest,
    DurableResumeRequest,
)
from my_agents.conversations.durable_store import claim_job, record_worker_heartbeat, renew_lease
from my_agents.conversations.models import AgentRunModel, MessageModel
from my_agents.conversations.schemas import (
    ConversationRunInterruptedResponse,
    ConversationRunRequest,
    ConversationRunResponse,
    ReasoningSummaryDeltaEventData,
)
from my_agents.knowledge.auth import resolve_conversation_knowledge_context
from my_agents.knowledge.retrieval import RetrievalService
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    supports_background_sessions,
)
from my_agents.reasoning import EffectiveReasoningPreferences
from my_agents.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def _events(db, job, run, principal, graph, settings, provider) -> Iterator[str]:
    payload = json.loads(job.request_json)
    selection = resolve_conversation_knowledge_context(
        db, principal=principal, requested_selection=run_knowledge_base_selection(run)
    )
    if job.kind == "run":
        request = ConversationRunRequest.model_validate(payload["request"])
        message = db.get(MessageModel, run.user_message_id)
        if message is None:
            raise RuntimeError("admitted message missing")
        yield from conversation_run_events(
            db=db,
            admitted=AdmittedRun(run=run, user_message=message),
            conversation_id=run.conversation_id,
            request=request,
            user_id=run.user_id,
            principal=principal,
            selection_context=selection,
            graph_runner=graph,
            settings=settings,
            document_workspace_provider=provider,
            reasoning=EffectiveReasoningPreferences(
                mode=run.reasoning_mode, effort=run.reasoning_effort, model=run.assistant_model
            ),
        )
    elif job.kind == "resume":
        request = DurableResumeRequest.model_validate(payload["request"]).answer
        if getattr(request, "document_id", None):
            if not RetrievalService(db).document_is_user_selectable(
                user_id=principal.user_id,
                document_id=request.document_id,
                knowledge_base_ids=selection.retrieval_knowledge_base_ids,
            ):
                raise HTTPException(403, "Selected document is no longer available.")
            value = (
                {"document_id": request.document_id}
                if request.schema_version == 1
                else {"kind": "select", "document_id": request.document_id}
            )
        elif getattr(request, "attachment_ids", None):
            from my_agents.document_workspace.service import validate_attachment_selection

            interaction = json.loads(run.interaction_payload_json or "{}")
            if interaction.get("access") == "original":
                validate_attachment_selection(
                    db,
                    conversation_id=run.conversation_id,
                    owner_user_id=run.user_id,
                    attachment_ids=request.attachment_ids,
                    settings=settings,
                )
            value = {"kind": "select", "attachment_ids": request.attachment_ids}
        else:
            value = {"kind": "refine", "text": request.text}
        metadata = {
            "run_id": run.id,
            "status": "running",
            "interaction_id": request.interaction_id,
            "interaction_schema_version": request.schema_version,
            "interaction_type": request.type,
        }
        prepared = PreparedConversationRunResume(
            run=run,
            messages=messages_for_conversation(db, run.conversation_id),
            selection_context=selection,
            resume_value=value,
            resumed_event_payload=metadata,
        )
        yield sse_event("run_resumed", metadata)
        yield from resumed_conversation_run_events(
            db=db, prepared=prepared, graph_runner=graph, settings=settings
        )
    elif job.kind == "replay":
        request = DurableReplayRequest.model_validate(payload["request"])
        context = replay_context_for_request(
            db,
            conversation_id=run.conversation_id,
            message_id=payload["message_id"],
            principal=principal,
            settings=settings,
            request=request.request,
            execution_run_id=run.id,
        )
        context = replace(
            context,
            reasoning_mode=run.reasoning_mode,
            reasoning_effort=run.reasoning_effort,
            assistant_model=run.assistant_model,
        )
        from my_agents.api.conversations.transcripts import prune_conversation_from_message

        def prune_before_completion(completed_run: AgentRunModel) -> None:
            if completed_run.id != run.id:
                raise RuntimeError("durable replay completion does not match its run")
            prune_conversation_from_message(
                db,
                conversation_id=run.conversation_id,
                target_message=context.target_message,
                removed_messages=context.removed_messages,
                original_run=context.original_run,
                preserved_run_ids={run.id},
                commit=False,
            )
            db.info["durable_replay_pruned_run_id"] = run.id

        db.info["durable_before_completion_commit"] = prune_before_completion
        try:
            yield from replay_conversation_run_events(
                db=db,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                replay_context=context,
                graph_runner=graph,
                run=run,
            )
        finally:
            db.info.pop("durable_before_completion_commit", None)
            db.info.pop("durable_replay_pruned_run_id", None)
    else:
        raise RuntimeError("unknown durable operation")


def _parse(frame: str) -> tuple[str, dict]:
    name = None
    data = []
    for line in frame.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if name is None:
        raise ValueError("internal stream frame has no event")
    value = json.loads("\n".join(data))
    if not isinstance(value, dict):
        raise ValueError("internal stream payload is not an object")
    return name, value


def execute_job(
    settings: Settings,
    job_id: str,
    token: str,
    *,
    graph,
    provider=None,
    worker_id: str = "answer-worker",
) -> None:
    stop = threading.Event()

    def pulse():
        while not stop.wait(settings.durable_run_lease_seconds / 3):
            try:
                if not renew_lease(
                    settings.database_url, job_id, token, settings.durable_run_lease_seconds
                ):
                    return
                record_worker_heartbeat(settings.database_url, worker_id)
            except Exception:
                # Losing the lease prevents all subsequent worker commits.
                return

    thread = threading.Thread(target=pulse, name="answer-worker-lease", daemon=True)
    thread.start()
    failure = None
    try:
        with _sessionmaker_for_url(settings.database_url)() as db:
            job = db.get(DurableRunJob, job_id)
            if job is None or job.lease_token != token or job.status != "running":
                return
            run = db.get(AgentRunModel, job.run_id)
            if run is None:
                return
            changed = db.execute(
                update(AgentRunModel)
                .where(AgentRunModel.id == run.id, AgentRunModel.status == "queued")
                .values(status="running")
            ).rowcount
            db.commit()
            if changed != 1:
                failure = ("run_cancelled", "The answer was stopped before execution.")
                return
            db.refresh(run)
            install_fence(db, job_id, token)
            db.info["durable_execution"] = True
            user = db.get(UserModel, run.user_id)
            if user is None or user.approval_status != "approved":
                raise HTTPException(403, "Account is not available.")
            principal = Principal(
                user_id=user.id,
                session_id="durable-worker",
                is_guest=user.account_type == "guest",
                user_type=user.user_type,
            )
            assert_guest_access_active(db, principal)
            db.info["document_workspace_provider"] = provider
            parts: list[str] = []
            last_flush = time.monotonic()
            delta_sequence = 0
            total_chars = 0
            phase_ended = False

            def flush():
                nonlocal last_flush, delta_sequence
                if not parts:
                    return
                text = "".join(parts)
                parts.clear()
                delta_sequence += 1
                emit(
                    db,
                    run,
                    "answer_delta",
                    {"delta": text, "sequence": delta_sequence},
                    max_frames=settings.durable_run_max_frames,
                )
                db.commit()
                last_flush = time.monotonic()

            iterator = _events(db, job, run, principal, graph, settings, provider)
            try:
                for frame in iterator:
                    name, data = _parse(frame)
                    if name == "answer_delta":
                        text = data.get("delta")
                        if not isinstance(text, str):
                            raise ValueError("invalid answer delta")
                        total_chars += len(text)
                        if total_chars > 2_000_000:
                            raise ValueError("answer exceeds durable output limit")
                        for start in range(0, len(text), 4096):
                            parts.append(text[start : start + 4096])
                            if (
                                sum(map(len, parts)) >= 4096
                                or (time.monotonic() - last_flush) * 1000
                                >= settings.durable_run_batch_ms
                            ):
                                flush()
                        continue
                    flush()
                    if name == "run_error":
                        continue  # Safe run_failed is the sole durable failure control.
                    if name == "run_failed":
                        db.refresh(run)
                        if run.status in {"completed", "waiting_for_input", "cancelled"}:
                            # A legacy serializer can fail after the canonical commit.
                            # Delivery must describe saved state, not that late exception.
                            if repair_terminal(db, run):
                                db.commit()
                            phase_ended = True
                            continue
                        data = DurableFailure(
                            run_id=run.id,
                            conversation_id=run.conversation_id,
                            code="run_failed",
                            detail="The answer could not be completed.",
                        ).model_dump(mode="json")
                    elif name == "run_completed":
                        data = ConversationRunResponse.model_validate(data).model_dump(mode="json")
                    elif name == "run_interrupted":
                        data = ConversationRunInterruptedResponse.model_validate(data).model_dump(
                            mode="json"
                        )
                    elif name == "run_cancelled":
                        data = DurableCancellation(
                            run_id=run.id, conversation_id=run.conversation_id
                        ).model_dump(mode="json")
                    elif name == "reasoning_summary_delta":
                        data = ReasoningSummaryDeltaEventData.model_validate(data).model_dump(
                            mode="json"
                        )
                    emit(db, run, name, data, max_frames=settings.durable_run_max_frames)
                    db.commit()
                    if name in {"run_completed", "run_failed", "run_cancelled", "run_interrupted"}:
                        phase_ended = True
                flush()
                if not phase_ended:
                    db.refresh(run)
                    if repair_terminal(db, run):
                        db.commit()
                    if run.status not in {"completed", "failed", "cancelled", "waiting_for_input"}:
                        raise RuntimeError("execution ended without terminal state")
            finally:
                iterator.close()
    except ExecutionStopped:
        logger.info("answer_worker.ownership_lost job_id=%s", job_id)
        failure = ("worker_lost", "The answer worker lost ownership before completion.")
    except Exception as exc:
        logger.warning("answer_worker.failed job_id=%s error_type=%s", job_id, type(exc).__name__)
        failure = ("run_failed", "The answer could not be completed.")
    finally:
        stop.set()
        thread.join(timeout=1)
        with _sessionmaker_for_url(settings.database_url)() as db:
            job = db.scalar(
                select(DurableRunJob).where(DurableRunJob.id == job_id).with_for_update()
            )
            if job and job.status == "running" and job.lease_token == token:
                run = db.get(AgentRunModel, job.run_id, populate_existing=True)
                if failure:
                    fail_job(db, job, code=failure[0], detail=failure[1])
                elif run is not None:
                    repair_terminal(db, run)
                    job.status = "done"
                    job.lease_token = None
                    job.lease_expires_at = None
                db.commit()


def process_once(
    settings: Settings, *, graph, provider=None, worker_id: str = "answer-worker"
) -> bool:
    record_worker_heartbeat(settings.database_url, worker_id)
    reap_lost_jobs(settings)
    claimed = claim_job(settings.database_url, settings.durable_run_lease_seconds)
    if not claimed:
        return False
    execute_job(settings, *claimed, graph=graph, provider=provider, worker_id=worker_id)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.durable_runs_enabled or not supports_background_sessions(settings.database_url):
        parser.error("Enable MY_AGENTS_DURABLE_RUNS_ENABLED with a persistent database.")
    initialize_database(settings)
    from my_agents.agents.general_assistant.graph import build_graph
    from my_agents.api.document_workspace import get_document_workspace_provider
    from my_agents.persistence.langgraph import open_langgraph_persistence

    resources = open_langgraph_persistence(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker_id = str(uuid.uuid4())
    graph = build_graph(
        checkpointer=resources.checkpointer,
        store=resources.store,
        document_selection_hitl_enabled=resources.checkpointer is not None,
    )
    provider = get_document_workspace_provider(settings)
    try:
        while not stop.is_set():
            process_once(settings, graph=graph, provider=provider, worker_id=worker_id)
            if args.once:
                break
            stop.wait(settings.durable_run_poll_seconds)
    finally:
        with _sessionmaker_for_url(settings.database_url)() as db:
            db.execute(
                delete(DurableWorkerHeartbeat).where(DurableWorkerHeartbeat.worker_id == worker_id)
            )
            db.commit()
        resources.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
