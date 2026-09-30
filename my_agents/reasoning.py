"""Provider-facing reasoning preference contracts and policy helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from my_agents.model_defaults import default_reasoning_effort
from my_agents.settings import ReasoningEffort, ReasoningMode, Settings

SUPPORTED_REASONING_MODES: tuple[ReasoningMode, ...] = ("standard", "pro")
# Frozen public vocabulary; adapt new models in normalize_reasoning_effort instead.
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
    model: str | None = None


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
    model: str | None = None,
) -> EffectiveReasoningPreferences:
    """Resolve optional client preferences without letting guests raise model cost."""
    default_effort = default_reasoning_effort(model or settings.openai_model)
    if is_guest:
        return EffectiveReasoningPreferences(
            mode="standard",
            effort=default_effort,
        )
    return EffectiveReasoningPreferences(
        mode=requested_mode or fallback_mode or "standard",
        effort=requested_effort or fallback_effort or default_effort,
    )


def reasoning_capability_response(
    *,
    settings: Settings,
    is_guest: bool,
    model: str | None = None,
) -> ReasoningCapabilityResponse:
    """Describe the stable UI enum plus support of the configured provider models."""
    model = model or settings.openai_model
    return ReasoningCapabilityResponse(
        customizable=not is_guest,
        default_effort=normalize_reasoning_effort(
            model=model, effort=default_reasoning_effort(model)
        ),
        supported_modes=list(SUPPORTED_REASONING_MODES),
        supported_efforts=list(SUPPORTED_REASONING_EFFORTS),
        chat=ReasoningSurfaceCapability(
            pro_supported=model_supports_reasoning_mode(model),
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
        for family in ("gpt-5.6", "gpt-6", "gpt-6.1-sol")
    )


def normalize_reasoning_effort(*, model: str, effort: ReasoningEffort) -> ReasoningEffort:
    """Map the frozen public effort vocabulary to model-supported provider values."""
    normalized = model.strip().casefold()
    if effort in {"none", "minimal"} and any(
        normalized == family or normalized.startswith(f"{family}-")
        for family in ("gpt-6.1-sol", "gpt-6-astra")
    ):
        return "low"
    if effort == "minimal" and any(
        normalized == family or normalized.startswith(f"{family}-")
        for family in ("gpt-5.6", "gpt-6")
    ):
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
