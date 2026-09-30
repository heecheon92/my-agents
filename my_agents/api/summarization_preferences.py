"""Registered-user model choice for internal conversation summarization."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from my_agents.api.errors import APIErrorCode, APIHTTPException
from my_agents.assistant_preferences import AssistantPreferencesResponse
from my_agents.auth.contracts import Principal
from my_agents.auth.dependencies import get_current_principal
from my_agents.auth.guest_limits import assert_guest_access_active
from my_agents.auth.models import UserModel
from my_agents.model_defaults import (
    EXPOSED_ASSISTANT_MODELS,
    SUPPORTED_ASSISTANT_MODELS,
    AssistantModelId,
)
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings, get_settings

router = APIRouter(tags=["summarization-preferences"])


class SummarizationPreferencesPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summarization_model: AssistantModelId | None


class SummarizationModelCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: AssistantModelId
    name: str


class SummarizationModelsCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customizable: bool
    default_model: str
    recommended_model: str = "gpt-6-luna"
    models: list[SummarizationModelCapability]


def effective_summarization_model(db: Session, user_id: str, settings: Settings) -> str:
    user = db.get(UserModel, user_id)
    if (
        user is not None
        and user.account_type != "guest"
        and user.summarization_model_preference in SUPPORTED_ASSISTANT_MODELS
    ):
        return user.summarization_model_preference
    return settings.summarization_model


def preferences(
    db: Session, principal: Principal, settings: Settings
) -> AssistantPreferencesResponse:
    user = db.get(UserModel, principal.user_id)
    selected = user.summarization_model_preference if user and not principal.is_guest else None
    if selected not in SUPPORTED_ASSISTANT_MODELS:
        selected = None
    return AssistantPreferencesResponse(
        customizable=not principal.is_guest,
        default_model=settings.summarization_model,
        selected_model=selected,
        effective_model=selected or settings.summarization_model,
    )


@router.get("/summarization/preferences")
def get_preferences(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AssistantPreferencesResponse:
    assert_guest_access_active(db, principal)
    return preferences(db, principal, settings)


@router.patch("/summarization/preferences")
def patch_preferences(
    request: SummarizationPreferencesPatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AssistantPreferencesResponse:
    assert_guest_access_active(db, principal)
    if principal.is_guest:
        raise APIHTTPException(
            status_code=403,
            detail="Guests cannot select a summarization model",
            code=APIErrorCode.PERMISSION_DENIED,
        )
    user = db.get(UserModel, principal.user_id)
    assert user is not None
    user.summarization_model_preference = request.summarization_model
    db.commit()
    return preferences(db, principal, settings)


@router.get("/capabilities/summarization-models")
def get_models(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SummarizationModelsCapabilityResponse:
    assert_guest_access_active(db, principal)
    return SummarizationModelsCapabilityResponse(
        customizable=not principal.is_guest,
        default_model=settings.summarization_model,
        models=[
            SummarizationModelCapability(
                id=model,
                name=model.replace("gpt-", "GPT-")
                .replace("-luna", " Luna")
                .replace("-sol", " Sol")
                .replace("-astra", " Astra"),
            )
            for model in EXPOSED_ASSISTANT_MODELS
        ],
    )
