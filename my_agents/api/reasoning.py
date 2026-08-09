"""Authenticated reasoning capability and per-run policy boundary."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from my_agents.api.errors import APIErrorCode, APIHTTPException
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.reasoning import (
    EffectiveReasoningPreferences,
    ReasoningCapabilityResponse,
    effective_reasoning_preferences,
    model_supports_reasoning_mode,
    reasoning_capability_response,
)
from my_agents.settings import ReasoningEffort, ReasoningMode, Settings, get_settings

reasoning_router = APIRouter(tags=["reasoning"])


@reasoning_router.get(
    "/capabilities/reasoning",
    response_model=ReasoningCapabilityResponse,
)
def get_reasoning_capability(
    principal: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReasoningCapabilityResponse:
    """Return the active server default and the stable frontend option set."""
    return reasoning_capability_response(settings=settings, is_guest=principal.is_guest)


def resolve_reasoning_preferences(
    *,
    settings: Settings,
    principal: Principal,
    requested_mode: ReasoningMode | None,
    requested_effort: ReasoningEffort | None,
    uses_document_workspace: bool,
    fallback_mode: ReasoningMode | None = None,
    fallback_effort: ReasoningEffort | None = None,
) -> EffectiveReasoningPreferences:
    """Resolve and validate one run's effective provider-facing preferences."""
    preferences = effective_reasoning_preferences(
        settings=settings,
        is_guest=principal.is_guest,
        requested_mode=requested_mode,
        requested_effort=requested_effort,
        fallback_mode=fallback_mode,
        fallback_effort=fallback_effort,
    )
    model = settings.document_workspace_model if uses_document_workspace else settings.openai_model
    if preferences.mode == "pro" and not model_supports_reasoning_mode(model):
        raise APIHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pro reasoning mode requires a GPT-5.6 model",
            code=APIErrorCode.REASONING_MODE_NOT_SUPPORTED,
        )
    return preferences
