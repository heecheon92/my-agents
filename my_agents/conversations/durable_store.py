"""Transactional durable delivery and compare-and-set worker ownership primitives.

Session-taking functions never commit. Callers commit output, final transcript and run
state together, and must fence worker writes against their live ownership lease.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from my_agents.conversations.durable_models import (
    DurableRunFrame,
    DurableRunJob,
    DurableRunOutput,
    DurableWorkerHeartbeat,
)
from my_agents.conversations.models import AgentEventModel, AgentEventType
from my_agents.persistence.database import _sessionmaker_for_url

MAX_PARTIAL_REPLY_CHARACTERS = 2_000_000

CONTROL_EVENTS = frozenset({"run_completed", "run_failed", "run_cancelled", "run_interrupted"})


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_fingerprint(request_json: dict[str, Any]) -> str:
    """Hash semantic request fields, including conversation identity, not transport data."""
    return hashlib.sha256(_json(request_json).encode()).hexdigest()


def find_job(db: Session, user_id: str, kind: str, client_request_id: str) -> DurableRunJob | None:
    return db.scalar(
        select(DurableRunJob).where(
            DurableRunJob.user_id == user_id,
            DurableRunJob.kind == kind,
            DurableRunJob.client_request_id == client_request_id,
        )
    )


def ensure_output(db: Session, run_id: str) -> DurableRunOutput:
    """Initialize output during admission, before the run becomes worker-visible."""
    output = db.get(DurableRunOutput, run_id)
    if output is None:
        output = DurableRunOutput(
            run_id=run_id,
            cursor=0,
            min_cursor=0,
            snapshot_json=_json(
                {
                    "partial_reply": "",
                    "reasoning_summaries": [],
                    "activity_events": [],
                    "interaction": None,
                    "result": None,
                    "error": None,
                }
            ),
        )
        db.add(output)
        db.flush()
    return output


def enqueue_job(
    db: Session,
    *,
    run_id: str,
    user_id: str,
    kind: str,
    client_request_id: str,
    request_json: dict[str, Any],
) -> DurableRunJob:
    """Persist semantic input only; uniqueness violations belong to admission retry handling."""
    job = DurableRunJob(
        run_id=run_id,
        user_id=user_id,
        kind=kind,
        client_request_id=client_request_id,
        request_json=_json(request_json),
        request_fingerprint=request_fingerprint(request_json),
        status="queued",
    )
    db.add(job)
    ensure_output(db, run_id)
    db.flush()
    return job


def claim_job(database_url: str, lease_seconds: float) -> tuple[str, str] | None:
    """Atomically claim queued work once; expired running jobs are never regenerated."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    with _sessionmaker_for_url(database_url)() as db:
        # PostgreSQL row locks skip another worker; SQLite relies on the UPDATE CAS.
        candidates = db.scalars(
            select(DurableRunJob.id)
            .where(DurableRunJob.status == "queued")
            .order_by(DurableRunJob.created_at, DurableRunJob.id)
            .with_for_update(skip_locked=True)
            .limit(20)
        ).all()
        for job_id in candidates:
            token = str(uuid.uuid4())
            changed = db.execute(
                update(DurableRunJob)
                .where(DurableRunJob.id == job_id, DurableRunJob.status == "queued")
                .values(
                    status="running",
                    lease_token=token,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
                )
            ).rowcount
            if changed:
                db.commit()
                return job_id, token
        db.rollback()
    return None


def renew_lease(database_url: str, job_id: str, lease_token: str, lease_seconds: float) -> bool:
    """An already expired or superseded lease cannot be resurrected."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    now = datetime.now(UTC)
    with _sessionmaker_for_url(database_url)() as db:
        changed = db.execute(
            update(DurableRunJob)
            .where(
                DurableRunJob.id == job_id,
                DurableRunJob.status == "running",
                DurableRunJob.lease_token == lease_token,
                DurableRunJob.lease_expires_at > now,
            )
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
        ).rowcount
        db.commit()
        return bool(changed)


def record_worker_heartbeat(database_url: str, worker_id: str) -> None:
    """Worker IDs are process-unique, and a heartbeat contains no execution input."""
    with _sessionmaker_for_url(database_url)() as db:
        db.merge(DurableWorkerHeartbeat(worker_id=worker_id, last_seen_at=datetime.now(UTC)))
        db.commit()


def get_output(db: Session, run_id: str) -> DurableRunOutput | None:
    return db.get(DurableRunOutput, run_id, populate_existing=True)


def append_frame(
    db: Session,
    run_id: str,
    event: str,
    data: dict[str, Any],
    *,
    max_frames: int = 2000,
) -> DurableRunFrame:
    """Lock and update cursor, projection and frame as one caller-owned transaction."""
    if max_frames < 1:
        raise ValueError("max_frames must be positive")
    is_metadata = event not in CONTROL_EVENTS and event not in {
        "answer_delta",
        "reasoning_summary_delta",
    }
    if is_metadata:
        # Use the same closed allowlists as the audit API, without persisting any
        # transport content in its audit table. Unknown events fail closed.
        from my_agents.api.conversations.run_events import event_response

        typed_event = AgentEventType(event)
        safe = event_response(
            AgentEventModel(
                id=str(uuid.uuid4()),
                run_id=run_id,
                sequence=1,
                event_type=typed_event.value,
                payload_json=_json(data),
            )
        )
        data = safe.payload.model_dump(mode="json")
    # UPDATE acquires a row lock on PostgreSQL and the writer lock on SQLite before
    # reading the projection, avoiding stale-read overwrites on both databases.
    cursor = db.scalar(
        update(DurableRunOutput)
        .where(DurableRunOutput.run_id == run_id)
        .values(cursor=DurableRunOutput.cursor)
        .returning(DurableRunOutput.cursor)
        .execution_options(synchronize_session=False)
    )
    if cursor is None:
        raise LookupError("Durable output must be initialized during admission")
    output = get_output(db, run_id)
    assert output is not None
    projection = json.loads(output.snapshot_json)
    if event == "answer_delta" and (
        len(projection["partial_reply"]) + len(data["delta"]) > MAX_PARTIAL_REPLY_CHARACTERS
    ):
        raise ValueError("answer exceeds durable output limit")
    cursor += 1
    output.cursor = cursor
    if event == "answer_delta":
        projection["partial_reply"] += data["delta"]
    elif event == "reasoning_summary_delta":
        summaries = projection["reasoning_summaries"]
        summary = next((item for item in summaries if item["stage"] == data["stage"]), None)
        if summary is None:
            summary = {"stage": data["stage"], "text": "", "source": "provider_reasoning_summary"}
            summaries.append(summary)
        summary["text"] = (summary["text"] + data["delta"])[:500]
    elif event == "reasoning_summary_generated":
        summaries = projection["reasoning_summaries"]
        summaries[:] = [item for item in summaries if item["stage"] != data["stage"]]
        summaries.append(data)
    elif event == "run_completed":
        projection["result"] = data
        projection["interaction"] = None
        projection["error"] = None
        projection["reasoning_summaries"] = data.get("reasoning_summaries", [])
    elif event == "run_interrupted":
        projection["interaction"] = data.get("interaction")
    elif event == "run_failed":
        projection["error"] = {"code": data["code"], "detail": data["detail"]}
        projection["interaction"] = None
    elif event == "run_cancelled":
        projection["interaction"] = None
    elif event == "run_resumed":
        projection["interaction"] = None
        projection["error"] = None
        output.terminal_event = None
        output.terminal_data_json = None
        output.terminal_cursor = None
    if is_metadata:
        projection["activity_events"].append({"id": cursor, "event": event, "data": data})
        projection["activity_events"] = projection["activity_events"][-max_frames:]
    if event in CONTROL_EVENTS:
        output.terminal_event = event
        output.terminal_data_json = _json(data)
        output.terminal_cursor = cursor
    output.snapshot_json = _json(projection)
    frame = DurableRunFrame(run_id=run_id, cursor=cursor, event=event, data_json=_json(data))
    db.add(frame)
    cutoff = max(0, cursor - max_frames)
    if cutoff > output.min_cursor:
        db.execute(
            delete(DurableRunFrame).where(
                DurableRunFrame.run_id == run_id, DurableRunFrame.cursor <= cutoff
            )
        )
        output.min_cursor = cutoff
    db.flush()
    return frame


def list_frames(db: Session, run_id: str, after: int, limit: int = 500) -> list[DurableRunFrame]:
    return list(
        db.scalars(
            select(DurableRunFrame)
            .where(DurableRunFrame.run_id == run_id, DurableRunFrame.cursor > after)
            .order_by(DurableRunFrame.cursor)
            .limit(limit)
        )
    )


def latest_terminal(db: Session, run_id: str) -> DurableRunFrame | None:
    """Reconstruct the retained close signal even if its frame was pruned."""
    output = get_output(db, run_id)
    if output is None or output.terminal_event is None:
        return None
    return DurableRunFrame(
        run_id=run_id,
        cursor=output.terminal_cursor,
        event=output.terminal_event,
        data_json=output.terminal_data_json,
    )
