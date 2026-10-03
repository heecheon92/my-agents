"""Authenticated model discovery and registered-user preference endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from my_agents.api.errors import APIErrorCode, APIHTTPException
from my_agents.assistant_preferences import (
    AssistantPreferencesPatchRequest,
    AssistantPreferencesResponse,
    assistant_preferences_response,
    default_assistant_model,
)
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active
from my_agents.auth.models import UserModel
from my_agents.model_defaults import (
    EXPOSED_ASSISTANT_MODELS,
    AssistantModelId,
    default_reasoning_effort,
)
from my_agents.persistence.database import get_database_session
from my_agents.reasoning import model_supports_reasoning_mode, normalize_reasoning_effort
from my_agents.settings import ReasoningEffort, Settings, get_settings

assistant_preferences_router = APIRouter(tags=["assistant-preferences"])


class AssistantModelCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: AssistantModelId
    name: str
    default_reasoning_effort: ReasoningEffort
    pro_supported: bool


class AssistantModelsCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customizable: bool
    default_model: str
    models: list[AssistantModelCapability]


@assistant_preferences_router.get("/capabilities/assistant-models")
def get_assistant_models(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AssistantModelsCapabilityResponse:
    assert_guest_access_active(db, principal)
    return AssistantModelsCapabilityResponse(
        customizable=not principal.is_guest,
        default_model=default_assistant_model(principal, settings),
        models=[
            AssistantModelCapability(
                id=model,
                name=model.replace("gpt-", "GPT-")
                .replace("-luna", " Luna")
                .replace("-sol", " Sol")
                .replace("-astra", " Astra"),
                default_reasoning_effort=normalize_reasoning_effort(
                    model=model, effort=default_reasoning_effort(model)
                ),
                pro_supported=model_supports_reasoning_mode(model),
            )
            for model in EXPOSED_ASSISTANT_MODELS
        ],
    )


@assistant_preferences_router.get("/assistant/preferences")
def get_assistant_preferences(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AssistantPreferencesResponse:
    assert_guest_access_active(db, principal)
    return assistant_preferences_response(db, principal, settings)


@assistant_preferences_router.patch("/assistant/preferences")
def patch_assistant_preferences(
    request: AssistantPreferencesPatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AssistantPreferencesResponse:
    assert_guest_access_active(db, principal)
    if principal.is_guest:
        raise APIHTTPException(
            status_code=403,
            detail="Guests cannot select an assistant model",
            code=APIErrorCode.PERMISSION_DENIED,
        )
    user = db.get(UserModel, principal.user_id)
    assert user is not None
    user.assistant_model_preference = request.assistant_model
    db.commit()
    return assistant_preferences_response(db, principal, settings)
