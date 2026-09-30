"""Product DB ownership and public contracts for per-user assistant model preferences."""

from typing import cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.models import UserModel
from my_agents.model_defaults import SUPPORTED_ASSISTANT_MODELS, AssistantModelId
from my_agents.settings import Settings


class AssistantPreferencesPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_model: AssistantModelId | None


class AssistantPreferencesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customizable: bool
    default_model: str
    selected_model: AssistantModelId | None
    effective_model: str


def selected_assistant_model(db: Session, principal: Principal) -> AssistantModelId | None:
    """Validate API support independently of frontend visibility; guests ignore overrides."""
    if principal.is_guest:
        return None
    user = db.get(UserModel, principal.user_id)
    selected = user.assistant_model_preference if user is not None else None
    return cast(AssistantModelId, selected) if selected in SUPPORTED_ASSISTANT_MODELS else None


def effective_assistant_model(db: Session, principal: Principal, settings: Settings) -> str:
    return selected_assistant_model(db, principal) or settings.openai_model


def assistant_preferences_response(
    db: Session, principal: Principal, settings: Settings
) -> AssistantPreferencesResponse:
    selected = selected_assistant_model(db, principal)
    return AssistantPreferencesResponse(
        customizable=not principal.is_guest,
        default_model=settings.openai_model,
        selected_model=selected,
        effective_model=selected or settings.openai_model,
    )
