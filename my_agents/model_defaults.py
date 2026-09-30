"""Application-owned model catalog and reasoning defaults, seeded from provider guidance."""

from typing import Literal, get_args

AssistantModelId = Literal[
    "gpt-5.6-luna",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-6-sol",
    "gpt-6.1-sol",
    "gpt-6-astra",
]
SUPPORTED_ASSISTANT_MODELS: tuple[AssistantModelId, ...] = get_args(AssistantModelId)
# Product-facing choices are separate from the broader API compatibility contract.
EXPOSED_ASSISTANT_MODELS: tuple[AssistantModelId, ...] = (
    "gpt-6.1-sol",
    "gpt-6-luna",
    "gpt-6-astra",
)


def validate_exposed_assistant_models(supported: tuple[str, ...], exposed: tuple[str, ...]) -> None:
    """Fail early if discovery advertises a model the API cannot accept."""
    unsupported = set(exposed) - set(supported)
    if unsupported:
        raise ValueError(f"Exposed assistant models must be supported: {sorted(unsupported)}")


validate_exposed_assistant_models(SUPPORTED_ASSISTANT_MODELS, EXPOSED_ASSISTANT_MODELS)

# Initial Luna/Sol seeds verified against official model pages on 2026-09-30.
# Keep individual entries even when recommendations coincide.
# These are editable application policy; Astra retains the application's medium seed.
DefaultReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
MODEL_DEFAULT_REASONING_EFFORT: dict[str, DefaultReasoningEffort] = {
    "gpt-5.6-luna": "medium",
    "gpt-6-luna": "medium",
    "gpt-6-sol": "medium",
    "gpt-5.6-sol": "medium",
    "gpt-6.1-sol": "medium",
    "gpt-6-astra": "medium",
}


def default_reasoning_effort(model: str) -> DefaultReasoningEffort:
    """Look up a model or snapshot; retain the legacy default for other model IDs."""
    normalized = model.strip().casefold()
    for supported_model, effort in MODEL_DEFAULT_REASONING_EFFORT.items():
        if normalized == supported_model or normalized.startswith(f"{supported_model}-"):
            return effort
    return "medium"
