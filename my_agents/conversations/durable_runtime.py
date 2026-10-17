"""Ownership fencing, terminal reconciliation, and safe durable-run recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from my_agents.conversations.durable_models import (
    DurableRunJob,
    DurableRunOutput,
    DurableWorkerHeartbeat,
)
from my_agents.conversations.durable_store import append_frame, ensure_output
from my_agents.conversations.models import AgentRunModel
from my_agents.persistence.database import _sessionmaker_for_url, supports_background_sessions
from my_agents.settings import Settings

TERMINAL = {"completed", "failed", "cancelled", "waiting_for_input"}


class ExecutionStopped(BaseException):
    """Do not let legacy provider exception handlers turn lost ownership into a new write."""


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def worker_ready(settings: Settings) -> bool:
    if not settings.durable_runs_enabled or not supports_background_sessions(settings.database_url):
        return False
    try:
        with _sessionmaker_for_url(settings.database_url)() as db:
            return (
                db.scalar(
                    select(DurableWorkerHeartbeat.worker_id)
                    .where(
                        DurableWorkerHeartbeat.last_seen_at
                        > datetime.now(UTC) - timedelta(seconds=settings.durable_run_lease_seconds)
                    )
                    .limit(1)
                )
                is not None
            )
    except SQLAlchemyError:
        return False


def install_fence(db: Session, job_id: str, token: str) -> None:
    """Fence commits and terminal flushes, using the shared job -> run -> output lock order."""

    def own(session: Session) -> None:
        changed = session.execute(
            update(DurableRunJob)
            .where(
                DurableRunJob.id == job_id,
                DurableRunJob.status == "running",
                DurableRunJob.lease_token == token,
                DurableRunJob.lease_expires_at > datetime.now(UTC),
            )
            .values(lease_token=token),
            execution_options={"synchronize_session": False},
        ).rowcount
        if changed != 1:
            raise ExecutionStopped("worker ownership expired")

    def before_flush(session: Session, flush_context, instances) -> None:
        from sqlalchemy import inspect

        own(session)
        for obj in list(session.dirty):
            if (
                not isinstance(obj, AgentRunModel)
                or obj.status not in TERMINAL
                or not inspect(obj).attrs.status.history.has_changes()
            ):
                continue
            own(session)
            allowed = (
                ["running"]
                if obj.status in {"completed", "waiting_for_input"}
                else ["running", "cancelling", "queued"]
            )
            changed = session.execute(
                update(AgentRunModel)
                .where(AgentRunModel.id == obj.id, AgentRunModel.status.in_(allowed))
                .values(status=obj.status),
                execution_options={"synchronize_session": False},
            ).rowcount
            if changed != 1:
                raise ExecutionStopped("run terminal transition lost")

    db.info["durable_fence"] = own
    event.listen(db, "before_commit", own)
    event.listen(db, "before_flush", before_flush)


def emit(db: Session, run: AgentRunModel, name: str, data: dict, *, max_frames: int = 2000) -> None:
    """Serialize append and terminal repair; callers own commit."""
    if guard := db.info.get("durable_fence"):
        guard(db)
    db.flush()  # Fence terminal changes before acquiring the output row.
    ensure_output(db, run.id)
    db.execute(
        update(DurableRunOutput)
        .where(DurableRunOutput.run_id == run.id)
        .values(cursor=DurableRunOutput.cursor)
    )
    output = db.get(DurableRunOutput, run.id, populate_existing=True)
    if (
        name in {"run_completed", "run_failed", "run_cancelled", "run_interrupted"}
        and output.terminal_event == name
    ):
        return
    append_frame(db, run.id, name, data, max_frames=max_frames)


def repair_terminal(db: Session, run: AgentRunModel) -> bool:
    """Recover a terminal delivery if a process died after canonical transcript commit."""
    if run.status not in TERMINAL:
        return False
    if guard := db.info.get("durable_fence"):
        guard(db)
    db.execute(
        update(AgentRunModel).where(AgentRunModel.id == run.id).values(status=AgentRunModel.status)
    )
    db.refresh(run)
    if run.status not in TERMINAL:
        return False
    output = db.get(DurableRunOutput, run.id, populate_existing=True)
    if output is None:
        return False
    expected = {
        "completed": "run_completed",
        "failed": "run_failed",
        "cancelled": "run_cancelled",
        "waiting_for_input": "run_interrupted",
    }[run.status]
    if output.terminal_event == expected:
        return False
    if run.status == "waiting_for_input":
        from my_agents.api.conversations.interactions import interrupted_run_response

        data = interrupted_run_response(run).model_dump(mode="json")
    elif run.status == "completed":
        from my_agents.api.conversations.serializers import run_detail_response

        data = run_detail_response(db, run).model_dump(mode="json")
    elif run.status == "cancelled":
        data = {
            "run_id": run.id,
            "conversation_id": run.conversation_id,
            "status": "cancelled",
            "partial_reply_persisted": False,
        }
    else:
        data = {
            "run_id": run.id,
            "conversation_id": run.conversation_id,
            "status": "failed",
            "code": "run_failed",
            "detail": "The answer could not be completed.",
        }
    emit(db, run, expected, data)
    return True


def fail_job(db: Session, job: DurableRunJob, *, code: str, detail: str) -> None:
    run = db.get(AgentRunModel, job.run_id, populate_existing=True)
    if run is None:
        job.status = "done"
        return
    if run.status in {"queued", "running", "cancelling"}:
        if run.status == "cancelling":
            run.status = "cancelled"
            emit(
                db,
                run,
                "run_cancelled",
                {
                    "run_id": run.id,
                    "conversation_id": run.conversation_id,
                    "status": "cancelled",
                    "partial_reply_persisted": False,
                },
            )
        else:
            run.status = "failed"
            emit(
                db,
                run,
                "run_failed",
                {
                    "run_id": run.id,
                    "conversation_id": run.conversation_id,
                    "status": "failed",
                    "code": code,
                    "detail": detail,
                },
            )
    else:
        repair_terminal(db, run)
    from my_agents.api.conversations.run_events import append_run_event
    from my_agents.conversations.models import AgentEventType

    if run.status == "failed":
        append_run_event(
            db,
            run.id,
            AgentEventType.RUN_FAILED,
            {"safe_error_type": code, "safe_reason": detail},
            commit=False,
        )
    job.status = "done"
    job.lease_token = None
    job.lease_expires_at = None


def reap_lost_jobs(settings: Settings) -> None:
    now = datetime.now(UTC)
    with _sessionmaker_for_url(settings.database_url)() as db:
        ids = db.scalars(
            select(DurableRunJob.id).where(
                ((DurableRunJob.status == "running") & (DurableRunJob.lease_expires_at < now))
                | (
                    (DurableRunJob.status == "queued")
                    & (
                        DurableRunJob.created_at
                        < now - timedelta(seconds=settings.durable_run_queue_timeout_seconds)
                    )
                )
            )
        ).all()
        for job_id in ids:
            expired_predicate = (
                (DurableRunJob.status == "running") & (DurableRunJob.lease_expires_at < now)
            ) | (
                (DurableRunJob.status == "queued")
                & (
                    DurableRunJob.created_at
                    < now - timedelta(seconds=settings.durable_run_queue_timeout_seconds)
                )
            )
            claimed = db.execute(
                update(DurableRunJob)
                .where(DurableRunJob.id == job_id, expired_predicate)
                .values(status="done")
            )
            if claimed.rowcount != 1:
                continue
            job = db.get(DurableRunJob, job_id, populate_existing=True)
            fail_job(
                db,
                job,
                code="worker_lost",
                detail=(
                    "The answer worker stopped before completion. You can regenerate the answer."
                ),
            )
        waiting = db.scalars(
            select(AgentRunModel.id)
            .join(DurableRunOutput, DurableRunOutput.run_id == AgentRunModel.id)
            .where(
                AgentRunModel.status == "waiting_for_input",
                AgentRunModel.interaction_expires_at < now,
            )
        ).all()
        for run_id in waiting:
            changed = db.execute(
                update(AgentRunModel)
                .where(
                    AgentRunModel.id == run_id,
                    AgentRunModel.status == "waiting_for_input",
                    AgentRunModel.interaction_expires_at < now,
                )
                .values(
                    status="cancelled",
                    interaction_id=None,
                    interaction_type=None,
                    interaction_payload_json=None,
                    interaction_expires_at=None,
                )
            )
            if changed.rowcount == 1:
                run = db.get(AgentRunModel, run_id, populate_existing=True)
                repair_terminal(db, run)
        db.commit()


def cancel_durable(db: Session, run: AgentRunModel) -> bool:
    """Return false for legacy runs. Serialize cancellation against finalization."""
    if db.get(DurableRunOutput, run.id) is None:
        return False
    db.scalars(
        select(DurableRunJob)
        .where(DurableRunJob.run_id == run.id, DurableRunJob.status.in_(["queued", "running"]))
        .with_for_update()
    ).all()
    run = db.scalar(
        select(AgentRunModel)
        .where(AgentRunModel.id == run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run.status in {"queued", "waiting_for_input"}:
        run.status = "cancelled"
        run.interaction_id = run.interaction_type = run.interaction_payload_json = None
        run.interaction_expires_at = None
        db.execute(
            update(DurableRunJob)
            .where(DurableRunJob.run_id == run.id, DurableRunJob.status == "queued")
            .values(status="done")
        )
        from my_agents.api.conversations.run_events import append_run_event
        from my_agents.conversations.models import AgentEventType

        append_run_event(
            db,
            run.id,
            AgentEventType.RUN_CANCELLED,
            {
                "run_id": run.id,
                "conversation_id": run.conversation_id,
                "status": "cancelled",
                "partial_reply_persisted": False,
            },
            commit=False,
        )
        emit(
            db,
            run,
            "run_cancelled",
            {
                "run_id": run.id,
                "conversation_id": run.conversation_id,
                "status": "cancelled",
                "partial_reply_persisted": False,
            },
        )
    elif run.status == "running":
        from my_agents.api.conversations.run_events import append_run_event
        from my_agents.conversations.models import AgentEventType

        run.status = "cancelling"
        append_run_event(
            db,
            run.id,
            AgentEventType.RUN_CANCEL_REQUESTED,
            {"run_id": run.id, "status": "cancelling"},
            commit=False,
        )
    db.commit()
    return True


def delete_output_rows(db: Session, run_ids) -> None:
    from sqlalchemy import delete

    from my_agents.conversations.durable_models import DurableRunFrame

    for model in (DurableRunFrame, DurableRunJob, DurableRunOutput):
        db.execute(delete(model).where(model.run_id.in_(run_ids)))
