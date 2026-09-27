"""Model compatibility for persisted preferences and direct provider payloads."""

from typing import get_args

import pytest

from my_agents.api.reasoning import resolve_reasoning_preferences
from my_agents.auth.contracts import Principal
from my_agents.reasoning import (
    SUPPORTED_REASONING_EFFORTS,
    model_supports_reasoning_mode,
    normalize_reasoning_effort,
    openai_reasoning_payload,
    reasoning_capability_response,
)
from my_agents.settings import ReasoningEffort, Settings


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
        "gpt-5.6-sol-2026-08-01",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-6-astra",
        "gpt-6-sol-2026-09-22",
    ],
)
def test_supported_families_pro_and_minimal_payload(model):
    assert model_supports_reasoning_mode(model)
    assert openai_reasoning_payload(model=model, mode="pro", effort="minimal") == {
        "effort": "low",
        "mode": "pro",
        "summary": "auto",
    }


@pytest.mark.parametrize("model", ["gpt-5.5", "gpt-60-sol", "other-gpt-6-sol"])
def test_other_models_do_not_gain_pro(model):
    assert not model_supports_reasoning_mode(model)


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5.60-sol", "other-gpt-5.6-sol"])
def test_other_models_are_not_normalized(model):
    assert normalize_reasoning_effort(model=model, effort="minimal") == "minimal"


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-sol"])
@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("origin", ["request", "replay", "default", "guest"])
def test_effective_effort_normalized_for_selected_surface(model, workspace, origin):
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL="gpt-5.5" if workspace else model,
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL=model if workspace else "gpt-5.5",
        MY_AGENTS_OPENAI_REASONING_EFFORT="minimal",
    )
    result = resolve_reasoning_preferences(
        settings=settings,
        principal=Principal("offline", "offline", is_guest=origin == "guest"),
        requested_mode="pro",
        requested_effort="max" if origin == "guest" else "minimal" if origin == "request" else None,
        fallback_effort="minimal" if origin == "replay" else None,
        uses_document_workspace=workspace,
    )
    assert result.effort == "low"
    assert result.mode == ("standard" if origin == "guest" else "pro")


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-sol"])
def test_capabilities_report_pro_and_effective_default(model):
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL=model,
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL=model,
        MY_AGENTS_OPENAI_REASONING_EFFORT="minimal",
    )
    result = reasoning_capability_response(settings=settings, is_guest=False)
    assert result.chat.pro_supported and result.document_workspace.pro_supported
    assert result.default_effort == "low"
    assert "minimal" in result.supported_efforts


def test_none_still_omits_summary_for_sol():
    assert openai_reasoning_payload(model="gpt-6-sol", mode="standard", effort="none") == {
        "effort": "none",
        "mode": "standard",
    }


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-sol"])
def test_public_effort_contract_is_frozen_and_normalization_is_idempotent(model):
    expected = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    assert get_args(ReasoningEffort) == expected
    assert SUPPORTED_REASONING_EFFORTS == expected
    capability = reasoning_capability_response(
        settings=Settings(_env_file=None, MY_AGENTS_OPENAI_MODEL=model), is_guest=False
    )
    assert capability.supported_efforts == list(expected)
    for effort in expected:
        effective = normalize_reasoning_effort(model=model, effort=effort)
        assert effective == ("low" if effort == "minimal" else effort)
        assert normalize_reasoning_effort(model=model, effort=effective) == effective
