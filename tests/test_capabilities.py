"""Agent route capability metadata tests."""

from __future__ import annotations

from my_agents.agents.capabilities import get_capability_for_route, list_capabilities
from tests.conftest import ALLOWED_ROUTE_LABELS, invoke_graph


def test_capability_registry_covers_every_route_label() -> None:
    capabilities = list_capabilities()

    assert {capability.route_label for capability in capabilities} == ALLOWED_ROUTE_LABELS
    assert {capability.name for capability in capabilities}
    assert all(capability.purpose.strip() for capability in capabilities)


def test_capability_registry_uses_production_route_metadata() -> None:
    capabilities = list_capabilities()

    assert all(not capability.name.startswith("simulated_") for capability in capabilities)
    assert all(
        "simulation" not in capability.guidance_text().lower() for capability in capabilities
    )
    assert all("maturity" not in capability.guidance_text().lower() for capability in capabilities)


def test_graph_state_includes_capability_metadata_for_selected_route() -> None:
    result = invoke_graph("Help me organize my LangGraph study plan")

    capability = result["capability"]
    assert capability.route_label == "general_assistant"
    assert capability.name == get_capability_for_route("general_assistant").name
    assert "Capability `general_assistant_router`" in result["reply"]
    assert "simulation" not in result["reply"].lower()
    assert "not a real-world integration" not in result["reply"]


def test_research_helper_capability_documents_tool_boundary() -> None:
    capability = get_capability_for_route("research_helper")

    assert "web_search" in " ".join(capability.tools)
    assert capability.side_effects
