"""Model compatibility for persisted preferences and direct provider payloads."""

from typing import get_args

import pytest

from my_agents.api.reasoning import resolve_reasoning_preferences
from my_agents.auth.contracts import Principal
from my_agents.model_defaults import MODEL_DEFAULT_REASONING_EFFORT, default_reasoning_effort
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
        "gpt-6.1-sol",
        "gpt-6.1-sol-2026-09-30",
    ],
)
def test_supported_families_pro_and_minimal_payload(model):
    assert model_supports_reasoning_mode(model)
    assert openai_reasoning_payload(model=model, mode="pro", effort="minimal") == {
        "effort": "low",
        "mode": "pro",
        "summary": "auto",
    }


@pytest.mark.parametrize(
    "model", ["gpt-5.5", "gpt-60-sol", "other-gpt-6-sol", "gpt-6.10-sol", "gpt-6.1-luna"]
)
def test_other_models_do_not_gain_pro(model):
    assert not model_supports_reasoning_mode(model)


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5.60-sol", "other-gpt-5.6-sol"])
def test_other_models_are_not_normalized(model):
    assert normalize_reasoning_effort(model=model, effort="minimal") == "minimal"


@pytest.mark.parametrize("model", list(MODEL_DEFAULT_REASONING_EFFORT))
@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("origin", ["request", "replay", "default", "guest"])
def test_effective_effort_normalized_for_selected_surface(model, workspace, origin):
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL="gpt-5.5" if workspace else model,
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL=model if workspace else "gpt-5.5",
    )
    result = resolve_reasoning_preferences(
        settings=settings,
        principal=Principal("offline", "offline", is_guest=origin == "guest"),
        requested_mode="pro",
        requested_effort="max" if origin == "guest" else "minimal" if origin == "request" else None,
        fallback_effort="minimal" if origin == "replay" else None,
        uses_document_workspace=workspace,
    )
    assert result.effort == ("low" if origin in {"request", "replay"} else "medium")
    assert result.mode == ("standard" if origin == "guest" else "pro")


@pytest.mark.parametrize("model", list(MODEL_DEFAULT_REASONING_EFFORT))
def test_capabilities_report_pro_and_effective_default(model):
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL=model,
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL=model,
    )
    result = reasoning_capability_response(settings=settings, is_guest=False)
    assert result.chat.pro_supported and result.document_workspace.pro_supported
    assert result.default_effort == "medium"
    assert "minimal" in result.supported_efforts


def test_none_still_omits_summary_for_sol():
    assert openai_reasoning_payload(model="gpt-6-sol", mode="standard", effort="none") == {
        "effort": "none",
        "mode": "standard",
    }


@pytest.mark.parametrize("model", list(MODEL_DEFAULT_REASONING_EFFORT))
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
        mapped = effort == "minimal" or (
            model in {"gpt-6.1-sol", "gpt-6-astra"} and effort == "none"
        )
        assert effective == ("low" if mapped else effort)
        assert normalize_reasoning_effort(model=model, effort=effective) == effective


@pytest.mark.parametrize("model", list(MODEL_DEFAULT_REASONING_EFFORT))
def test_model_recommended_defaults_ignore_retired_env_override(model, monkeypatch):
    monkeypatch.setenv("MY_AGENTS_OPENAI_REASONING_EFFORT", "max")
    settings = Settings(_env_file=None, MY_AGENTS_OPENAI_MODEL=model)
    assert "openai_reasoning_effort" not in Settings.model_fields
    assert settings.openai_reasoning_effort == default_reasoning_effort(model) == "medium"
    assert default_reasoning_effort(f"{model}-2026-09-30") == "medium"


@pytest.mark.parametrize("mode", ["standard", "pro"])
@pytest.mark.parametrize("effort", SUPPORTED_REASONING_EFFORTS)
def test_gpt61_provider_payload_preserves_mode_and_uses_supported_effort(mode, effort):
    effective = "low" if effort in {"none", "minimal"} else effort
    assert openai_reasoning_payload(model="gpt-6.1-sol", mode=mode, effort=effort) == {
        "effort": effective,
        "mode": mode,
        "summary": "auto",
    }


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("effort", ["none", "minimal"])
def test_gpt61_replay_normalizes_against_current_surface_model(workspace, effort):
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL="gpt-5.6-sol" if workspace else "gpt-6.1-sol",
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL="gpt-6.1-sol" if workspace else "gpt-5.6-sol",
    )
    result = resolve_reasoning_preferences(
        settings=settings,
        principal=Principal("offline", "offline"),
        requested_mode=None,
        requested_effort=None,
        fallback_mode="pro",
        fallback_effort=effort,
        uses_document_workspace=workspace,
    )
    assert result.mode == "pro" and result.effort == "low"


def test_workspace_default_uses_selected_model_recommendation(monkeypatch):
    monkeypatch.setitem(MODEL_DEFAULT_REASONING_EFFORT, "gpt-6.1-sol", "low")
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL="gpt-5.6-sol",
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL="gpt-6.1-sol",
    )
    result = resolve_reasoning_preferences(
        settings=settings,
        principal=Principal("offline", "offline"),
        requested_mode=None,
        requested_effort=None,
        uses_document_workspace=True,
    )
    assert settings.openai_reasoning_effort == "medium" and result.effort == "low"
