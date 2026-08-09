"""Provider-facing reasoning preference contracts and policy helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from my_agents.settings import ReasoningEffort, ReasoningMode, Settings

SUPPORTED_REASONING_MODES: tuple[ReasoningMode, ...] = ("standard", "pro")
SUPPORTED_REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


@dataclass(frozen=True)
class EffectiveReasoningPreferences:
    """Effective server-enforced reasoning preferences for one conversation run."""

    mode: ReasoningMode
    effort: ReasoningEffort


class ReasoningModelCapability(BaseModel):
    """Reasoning-mode support for one configured OpenAI model role."""

    model_config = ConfigDict(extra="forbid")

    model: str
    pro_supported: bool


class ReasoningCapabilityResponse(BaseModel):
    """Frontend-facing reasoning controls and active server defaults."""

    model_config = ConfigDict(extra="forbid")

    customizable: bool
    default_mode: Literal["standard"] = "standard"
    default_effort: ReasoningEffort
    supported_modes: list[ReasoningMode]
    supported_efforts: list[ReasoningEffort]
    chat: ReasoningModelCapability
    document_workspace: ReasoningModelCapability


def effective_reasoning_preferences(
    *,
    settings: Settings,
    is_guest: bool,
    requested_mode: ReasoningMode | None,
    requested_effort: ReasoningEffort | None,
    fallback_mode: ReasoningMode | None = None,
    fallback_effort: ReasoningEffort | None = None,
) -> EffectiveReasoningPreferences:
    """Resolve optional client preferences without letting guests raise model cost."""
    if is_guest:
        return EffectiveReasoningPreferences(
            mode="standard",
            effort=settings.openai_reasoning_effort,
        )
    return EffectiveReasoningPreferences(
        mode=requested_mode or fallback_mode or "standard",
        effort=requested_effort or fallback_effort or settings.openai_reasoning_effort,
    )


def reasoning_capability_response(
    *,
    settings: Settings,
    is_guest: bool,
) -> ReasoningCapabilityResponse:
    """Describe the stable UI enum plus support of the configured provider models."""
    return ReasoningCapabilityResponse(
        customizable=not is_guest,
        default_effort=settings.openai_reasoning_effort,
        supported_modes=list(SUPPORTED_REASONING_MODES),
        supported_efforts=list(SUPPORTED_REASONING_EFFORTS),
        chat=ReasoningModelCapability(
            model=settings.openai_model,
            pro_supported=model_supports_reasoning_mode(settings.openai_model),
        ),
        document_workspace=ReasoningModelCapability(
            model=settings.document_workspace_model,
            pro_supported=model_supports_reasoning_mode(settings.document_workspace_model),
        ),
    )


def model_supports_reasoning_mode(model: str) -> bool:
    """Return whether the configured model belongs to the GPT-5.6 family."""
    normalized = model.strip().casefold()
    return normalized == "gpt-5.6" or normalized.startswith("gpt-5.6-")


def openai_reasoning_payload(
    *,
    model: str,
    mode: ReasoningMode,
    effort: ReasoningEffort,
) -> dict[str, str]:
    """Build the Responses API field, omitting mode for pre-GPT-5.6 models."""
    payload = {"effort": effort}
    if model_supports_reasoning_mode(model):
        payload["mode"] = mode
    return payload
