"""Authenticated long-term memory management endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from langgraph.store.base import BaseStore
from sqlalchemy.orm import Session

from my_agents.api.assistant import get_memory_store
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active
from my_agents.memory.models import MemorySuggestionModel, UserMemoryModel, UserMemorySettingsModel
from my_agents.memory.schemas import (
    UserMemoryCreateRequest,
    UserMemoryResponse,
    UserMemorySettingsPatchRequest,
    UserMemorySettingsResponse,
    UserMemorySuggestionCreateRequest,
    UserMemorySuggestionResponse,
)
from my_agents.memory.service import (
    MemoryDisabledError,
    MemoryNotFoundError,
    MemoryPolicyError,
    MemorySuggestionNotFoundError,
    MemorySuggestionUnavailableError,
    UserMemoryService,
    memory_namespace,
    memory_suggestion_value,
    memory_value,
)
from my_agents.memory.store_projection import (
    delete_projected_memory,
    project_memory,
    reconcile_memory_store,
)
from my_agents.observability.metrics import record_langgraph_persistence_operation
from my_agents.persistence.database import get_database_session

memories_router = APIRouter(prefix="/memories", tags=["memories"])
logger = logging.getLogger(__name__)


@memories_router.get("/settings", response_model=UserMemorySettingsResponse)
def get_memory_settings(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> UserMemorySettingsResponse:
    """Return the current user's opt-in memory setting."""
    assert_guest_access_active(db, principal)
    settings = UserMemoryService(db).get_or_create_settings(principal.user_id)
    return _settings_response(settings)


@memories_router.patch("/settings", response_model=UserMemorySettingsResponse)
def patch_memory_settings(
    request: UserMemorySettingsPatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    store: Annotated[BaseStore | None, Depends(get_memory_store)],
) -> UserMemorySettingsResponse:
    """Enable or disable long-term memory for the current user."""
    assert_guest_access_active(db, principal)
    settings = UserMemoryService(db).set_enabled(principal.user_id, request.enabled)
    if store is not None:
        try:
            reconcile_memory_store(
                db=db,
                store=store,
                apply=True,
                user_id=principal.user_id,
            )
        except Exception as exc:
            logger.warning(
                "memory_store.settings_reconcile_failed user_id=%s error_class=%s",
                principal.user_id,
                type(exc).__name__,
            )
            record_langgraph_persistence_operation(operation="memory_reconcile", outcome="failed")
    return _settings_response(settings)


@memories_router.post("", response_model=UserMemoryResponse, status_code=status.HTTP_201_CREATED)
def create_memory(
    request: UserMemoryCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    store: Annotated[BaseStore | None, Depends(get_memory_store)],
) -> UserMemoryResponse:
    """Persist an explicit user memory when the user has opted in."""
    assert_guest_access_active(db, principal)
    try:
        memory = UserMemoryService(db).store_explicit_memory(
            user_id=principal.user_id,
            content=request.content,
            category=request.category,
        )
    except MemoryDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="long-term memory is disabled",
        ) from exc
    except (MemoryPolicyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    _project_best_effort(store, memory)
    return _memory_response(memory)


@memories_router.get("", response_model=list[UserMemoryResponse])
def list_memories(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[UserMemoryResponse]:
    """List non-deleted memories owned by the current user."""
    assert_guest_access_active(db, principal)
    memories = UserMemoryService(db).list_memories(user_id=principal.user_id)
    return [_memory_response(memory) for memory in memories]


@memories_router.post("/suggestions", response_model=UserMemorySuggestionResponse, status_code=201)
def create_memory_suggestion(
    request: UserMemorySuggestionCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> UserMemorySuggestionResponse:
    """Create a pending memory suggestion without activating it."""
    assert_guest_access_active(db, principal)
    try:
        suggestion = UserMemoryService(db).create_memory_suggestion(
            user_id=principal.user_id,
            content=request.content,
            category=request.category,
        )
    except MemoryDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="long-term memory is disabled",
        ) from exc
    except (MemoryPolicyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _suggestion_response(suggestion)


@memories_router.get("/suggestions", response_model=list[UserMemorySuggestionResponse])
def list_memory_suggestions(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> list[UserMemorySuggestionResponse]:
    """List pending memory suggestions for the current user."""
    assert_guest_access_active(db, principal)
    suggestions = UserMemoryService(db).list_memory_suggestions(user_id=principal.user_id)
    return [_suggestion_response(suggestion) for suggestion in suggestions]


@memories_router.post("/suggestions/{suggestion_id}/confirm", response_model=UserMemoryResponse)
def confirm_memory_suggestion(
    suggestion_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    store: Annotated[BaseStore | None, Depends(get_memory_store)],
) -> UserMemoryResponse:
    """Confirm a pending suggestion and persist it as active memory."""
    assert_guest_access_active(db, principal)
    try:
        memory = UserMemoryService(db).confirm_memory_suggestion(
            user_id=principal.user_id, suggestion_id=suggestion_id
        )
    except MemorySuggestionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found"
        ) from exc
    except MemoryDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="long-term memory is disabled",
        ) from exc
    except MemorySuggestionUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _project_best_effort(store, memory)
    return _memory_response(memory)


@memories_router.post(
    "/suggestions/{suggestion_id}/reject", response_model=UserMemorySuggestionResponse
)
def reject_memory_suggestion(
    suggestion_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
) -> UserMemorySuggestionResponse:
    """Reject a pending suggestion so no active memory is created."""
    assert_guest_access_active(db, principal)
    try:
        suggestion = UserMemoryService(db).reject_memory_suggestion(
            user_id=principal.user_id, suggestion_id=suggestion_id
        )
    except MemorySuggestionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found"
        ) from exc
    except MemorySuggestionUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _suggestion_response(suggestion)


@memories_router.post("/{memory_id}/deactivate", response_model=UserMemoryResponse)
def deactivate_memory(
    memory_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    store: Annotated[BaseStore | None, Depends(get_memory_store)],
) -> UserMemoryResponse:
    """Deactivate a memory so it no longer enters provider context."""
    assert_guest_access_active(db, principal)
    try:
        memory = UserMemoryService(db).deactivate_memory(
            user_id=principal.user_id, memory_id=memory_id
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="memory not found"
        ) from exc
    _delete_projection_best_effort(store, memory)
    return _memory_response(memory)


@memories_router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    store: Annotated[BaseStore | None, Depends(get_memory_store)],
) -> Response:
    """Delete a memory owned by the current user and scrub stored content."""
    assert_guest_access_active(db, principal)
    try:
        memory = db.get(UserMemoryModel, memory_id)
        if memory is None or memory.user_id != principal.user_id:
            raise MemoryNotFoundError("memory not found")
        UserMemoryService(db).delete_memory(user_id=principal.user_id, memory_id=memory_id)
    except MemoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="memory not found"
        ) from exc
    _delete_projection_best_effort(store, memory)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _project_best_effort(store: BaseStore | None, memory: UserMemoryModel) -> None:
    if store is None:
        return
    try:
        project_memory(store, memory)
        record_langgraph_persistence_operation(operation="memory_project", outcome="completed")
    except Exception as exc:
        logger.warning(
            "memory_store.projection_failed memory_id=%s error_class=%s",
            memory.id,
            type(exc).__name__,
        )
        record_langgraph_persistence_operation(operation="memory_project", outcome="failed")


def _delete_projection_best_effort(store: BaseStore | None, memory: UserMemoryModel) -> None:
    if store is None:
        return
    try:
        delete_projected_memory(store, memory)
        record_langgraph_persistence_operation(operation="memory_delete", outcome="completed")
    except Exception as exc:
        logger.warning(
            "memory_store.delete_failed memory_id=%s error_class=%s",
            memory.id,
            type(exc).__name__,
        )
        record_langgraph_persistence_operation(operation="memory_delete", outcome="failed")


def _settings_response(settings: UserMemorySettingsModel) -> UserMemorySettingsResponse:
    return UserMemorySettingsResponse(enabled=settings.enabled, updated_at=settings.updated_at)


def _memory_response(memory: UserMemoryModel) -> UserMemoryResponse:
    return UserMemoryResponse(
        id=memory.id,
        namespace=list(memory_namespace(memory)),
        key=memory.key,
        category=memory.category,
        content=memory.content,
        value=memory_value(memory),
        status=memory.status,
        sensitivity=memory.sensitivity,
        provenance_type=memory.provenance_type,
        source_conversation_id=memory.source_conversation_id,
        source_message_id=memory.source_message_id,
        source_run_id=memory.source_run_id,
        source_document_id=memory.source_document_id,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        deactivated_at=memory.deactivated_at,
        deleted_at=memory.deleted_at,
        stale_at=memory.stale_at,
        stale_reason=memory.stale_reason,
    )


def _suggestion_response(suggestion: MemorySuggestionModel) -> UserMemorySuggestionResponse:
    return UserMemorySuggestionResponse(
        id=suggestion.id,
        category=suggestion.category,
        content=suggestion.content,
        value=memory_suggestion_value(suggestion),
        status=suggestion.status,
        sensitivity=suggestion.sensitivity,
        source_conversation_id=suggestion.source_conversation_id,
        source_message_id=suggestion.source_message_id,
        source_run_id=suggestion.source_run_id,
        source_document_id=suggestion.source_document_id,
        expires_at=suggestion.expires_at,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
        decided_at=suggestion.decided_at,
        memory_id=suggestion.memory_id,
    )
