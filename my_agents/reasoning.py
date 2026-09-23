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


class ReasoningSurfaceCapability(BaseModel):
    """Reasoning-mode support for one provider-facing response surface."""

    model_config = ConfigDict(extra="forbid")

    pro_supported: bool


class ReasoningCapabilityResponse(BaseModel):
    """Frontend-facing reasoning controls and active server defaults."""

    model_config = ConfigDict(extra="forbid")

    customizable: bool
    default_mode: Literal["standard"] = "standard"
    default_effort: ReasoningEffort
    supported_modes: list[ReasoningMode]
    supported_efforts: list[ReasoningEffort]
    chat: ReasoningSurfaceCapability
    document_workspace: ReasoningSurfaceCapability


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
        default_effort=normalize_reasoning_effort(
            model=settings.openai_model, effort=settings.openai_reasoning_effort
        ),
        supported_modes=list(SUPPORTED_REASONING_MODES),
        supported_efforts=list(SUPPORTED_REASONING_EFFORTS),
        chat=ReasoningSurfaceCapability(
            pro_supported=model_supports_reasoning_mode(settings.openai_model),
        ),
        document_workspace=ReasoningSurfaceCapability(
            pro_supported=model_supports_reasoning_mode(settings.document_workspace_model),
        ),
    )


def model_supports_reasoning_mode(model: str) -> bool:
    """Return whether the configured model supports Responses reasoning modes."""
    normalized = model.strip().casefold()
    return any(
        normalized == family or normalized.startswith(f"{family}-")
        for family in ("gpt-5.6", "gpt-6")
    )


def normalize_reasoning_effort(*, model: str, effort: ReasoningEffort) -> ReasoningEffort:
    """Keep minimal as a client compatibility alias for GPT-6 low effort."""
    normalized = model.strip().casefold()
    if effort == "minimal" and (normalized == "gpt-6" or normalized.startswith("gpt-6-")):
        return "low"
    return effort


def openai_reasoning_payload(
    *,
    model: str,
    mode: ReasoningMode,
    effort: ReasoningEffort,
) -> dict[str, str]:
    """Build the Responses API field and request a provider summary when applicable."""
    effort = normalize_reasoning_effort(model=model, effort=effort)
    payload = {"effort": effort}
    if model_supports_reasoning_mode(model):
        payload["mode"] = mode
    if effort != "none":
        payload["summary"] = "auto"
    return payload
