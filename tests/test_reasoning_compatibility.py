"""Model compatibility for persisted preferences and direct provider payloads."""

import pytest

from my_agents.api.reasoning import resolve_reasoning_preferences
from my_agents.auth.contracts import Principal
from my_agents.reasoning import (
    model_supports_reasoning_mode,
    openai_reasoning_payload,
    reasoning_capability_response,
)
from my_agents.settings import Settings


@pytest.mark.parametrize(
    "model", ["gpt-6-sol", "gpt-6-luna", "gpt-6-astra", "gpt-6-sol-2026-09-22"]
)
def test_gpt6_pro_and_minimal_payload(model):
    assert model_supports_reasoning_mode(model)
    assert openai_reasoning_payload(model=model, mode="pro", effort="minimal") == {
        "effort": "low",
        "mode": "pro",
        "summary": "auto",
    }


@pytest.mark.parametrize("model", ["gpt-5.5", "gpt-60-sol", "other-gpt-6-sol"])
def test_other_models_do_not_gain_pro(model):
    assert not model_supports_reasoning_mode(model)


def test_gpt56_behavior_preserved():
    assert openai_reasoning_payload(model="gpt-5.6-sol", mode="pro", effort="minimal") == {
        "effort": "minimal",
        "mode": "pro",
        "summary": "auto",
    }


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("origin", ["request", "replay", "default", "guest"])
def test_effective_effort_normalized_for_selected_surface(workspace, origin):
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL="gpt-5.6-sol" if workspace else "gpt-6-sol",
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL="gpt-6-sol" if workspace else "gpt-5.6-sol",
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


def test_capabilities_report_gpt6_pro_and_effective_default():
    settings = Settings(
        _env_file=None,
        MY_AGENTS_OPENAI_MODEL="gpt-6-sol",
        MY_AGENTS_DOCUMENT_WORKSPACE_MODEL="gpt-6-sol",
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
