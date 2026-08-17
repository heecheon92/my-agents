"""Persist and serialize public-safe LangGraph human interactions."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from my_agents.api.conversations.run_events import append_run_event
from my_agents.auth.models import UserModel
from my_agents.conversations.models import AgentEventType, AgentRunModel, RunStatus
from my_agents.conversations.schemas import (
    ConversationRunInterruptedResponse,
    PendingDocumentSelection,
)
from my_agents.observability.metrics import record_langgraph_persistence_operation

logger = logging.getLogger(__name__)


def graph_interrupt_payload(graph_state: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the first JSON-shaped interrupt value from a LangGraph result."""
    interrupts = graph_state.get("__interrupt__")
    if not isinstance(interrupts, (list, tuple)) or not interrupts:
        return None
    value = getattr(interrupts[0], "value", None)
    return value if isinstance(value, dict) else None


def persist_waiting_document_selection(
    *,
    db: Session,
    run: AgentRunModel,
    graph_state: dict[str, Any],
    wait_seconds: int,
) -> ConversationRunInterruptedResponse:
    """Persist a bounded public interaction while checkpoint state stays framework-owned."""
    payload = graph_interrupt_payload(graph_state)
    if payload is None:
        raise RuntimeError("interrupted graph did not expose a document-selection payload")
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=wait_seconds)
    user = db.get(UserModel, run.user_id)
    if user is not None and user.guest_expires_at is not None:
        guest_expires_at = _as_utc(user.guest_expires_at)
        expires_at = min(expires_at, guest_expires_at)
    interaction = PendingDocumentSelection.model_validate(
        {
            **payload,
            "expires_at": expires_at,
        }
    )
    run.status = RunStatus.WAITING_FOR_INPUT.value
    run.interaction_id = interaction.interaction_id
    run.interaction_type = interaction.type
    run.interaction_payload_json = json.dumps(interaction.model_dump(mode="json"), sort_keys=True)
    run.interaction_expires_at = expires_at
    append_run_event(
        db,
        run.id,
        AgentEventType.RUN_INTERRUPTED,
        {
            "run_id": run.id,
            "status": RunStatus.WAITING_FOR_INPUT.value,
            "interaction_id": interaction.interaction_id,
            "interaction_type": interaction.type,
            "option_count": interaction.option_count,
            "expires_at": expires_at.isoformat(),
        },
        commit=False,
    )
    db.commit()
    record_langgraph_persistence_operation(operation="interrupt", outcome="waiting")
    db.refresh(run)
    return ConversationRunInterruptedResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        interaction=interaction,
    )


def interrupted_run_response(run: AgentRunModel) -> ConversationRunInterruptedResponse:
    """Rebuild a refresh-safe waiting response from Product DB metadata."""
    try:
        payload = json.loads(run.interaction_payload_json or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("stored run interaction payload is invalid") from exc
    return ConversationRunInterruptedResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        interaction=PendingDocumentSelection.model_validate(payload),
    )


def delete_checkpoint_thread(graph_runner: object, run_id: str) -> None:
    """Best-effort deletion hook for a terminal Product DB run."""
    checkpointer = getattr(graph_runner, "checkpointer", None)
    delete_thread = getattr(checkpointer, "delete_thread", None)
    if callable(delete_thread):
        try:
            delete_thread(run_id)
            record_langgraph_persistence_operation(
                operation="checkpoint_delete", outcome="completed"
            )
        except Exception as exc:
            logger.warning(
                "langgraph.checkpoint_delete_failed run_id=%s error_class=%s",
                run_id,
                type(exc).__name__,
            )
            record_langgraph_persistence_operation(operation="checkpoint_delete", outcome="failed")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
