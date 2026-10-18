"""Product DB ownership and public contracts for per-user assistant model preferences."""

from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from my_agents.auth.contracts import Principal
from my_agents.auth.models import UserModel
from my_agents.model_defaults import SUPPORTED_ASSISTANT_MODELS, AssistantModelId
from my_agents.settings import Settings

DocumentRetrievalPriority = Literal["always", "automatic", "on_request"]
DocumentIngestionMode = Literal["standard", "ai_assisted"]


class AssistantPreferencesPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_model: AssistantModelId | None = Field(
        default=None, json_schema_extra=lambda schema: schema.pop("default", None)
    )
    document_retrieval_priority: DocumentRetrievalPriority | None = Field(
        default=None,
        description="Omit to preserve the current preference. Explicit null is not accepted.",
        json_schema_extra=lambda schema: schema.pop("default", None),
    )
    document_ingestion_mode: DocumentIngestionMode | None = Field(
        default=None,
        description="Omit to preserve the current preference. Explicit null is not accepted.",
        json_schema_extra=lambda schema: schema.pop("default", None),
    )

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one preference must be supplied")
        for name in ("document_retrieval_priority", "document_ingestion_mode"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class AssistantPreferencesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customizable: bool
    default_model: str
    selected_model: AssistantModelId | None
    effective_model: str
    document_retrieval_priority: DocumentRetrievalPriority = "automatic"
    document_ingestion_mode: DocumentIngestionMode = "standard"


def document_retrieval_priority(db: Session, user_id: str) -> DocumentRetrievalPriority:
    """Resolve the registered user's source preference; guests keep automatic routing."""
    user = db.get(UserModel, user_id)
    value = user.document_retrieval_priority if user and user.account_type != "guest" else None
    return (
        cast(DocumentRetrievalPriority, value) if value in {"always", "on_request"} else "automatic"
    )


def document_ingestion_mode(db: Session, principal: Principal) -> DocumentIngestionMode:
    """Resolve the default for a new ingestion; never changes existing documents."""
    if principal.is_guest:
        return "standard"
    user = db.get(UserModel, principal.user_id)
    return "ai_assisted" if user and user.document_ingestion_mode == "ai_assisted" else "standard"


def selected_assistant_model(db: Session, principal: Principal) -> AssistantModelId | None:
    """Validate API support independently of frontend visibility; guests ignore overrides."""
    if principal.is_guest:
        return None
    user = db.get(UserModel, principal.user_id)
    selected = user.assistant_model_preference if user is not None else None
    return cast(AssistantModelId, selected) if selected in SUPPORTED_ASSISTANT_MODELS else None


def default_assistant_model(principal: Principal, settings: Settings) -> str:
    """Guest answer policy is independent of the registered-user deployment fallback."""
    return settings.guest_assistant_model if principal.is_guest else settings.openai_model


def effective_assistant_model(db: Session, principal: Principal, settings: Settings) -> str:
    return selected_assistant_model(db, principal) or default_assistant_model(principal, settings)


def assistant_preferences_response(
    db: Session, principal: Principal, settings: Settings
) -> AssistantPreferencesResponse:
    selected = selected_assistant_model(db, principal)
    default = default_assistant_model(principal, settings)
    return AssistantPreferencesResponse(
        customizable=not principal.is_guest,
        default_model=default,
        selected_model=selected,
        effective_model=selected or default,
        document_retrieval_priority=(
            "automatic"
            if principal.is_guest
            else document_retrieval_priority(db, principal.user_id)
        ),
        document_ingestion_mode=document_ingestion_mode(db, principal),
    )
