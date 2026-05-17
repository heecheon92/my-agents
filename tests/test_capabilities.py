"""Agent capability metadata tests."""

from __future__ import annotations

from my_agents.agents.capabilities import get_capability_for_route, list_capabilities
from tests.conftest import ALLOWED_ROUTE_LABELS, invoke_graph


def test_capability_registry_covers_every_route_label() -> None:
    capabilities = list_capabilities()

    assert {capability.route_label for capability in capabilities} == ALLOWED_ROUTE_LABELS
    assert {capability.name for capability in capabilities}
    assert all(capability.purpose.strip() for capability in capabilities)


def test_simulated_capabilities_are_explicitly_marked_and_side_effect_free() -> None:
    simulated = [
        capability for capability in list_capabilities() if capability.mode == "simulation"
    ]

    assert simulated
    assert {capability.route_label for capability in simulated} >= {
        "learning_coach",
        "project_planner",
        "career_helper",
    }
    assert all(capability.side_effects == () for capability in simulated)


def test_graph_state_includes_capability_metadata_for_selected_route() -> None:
    result = invoke_graph("Help me study LangGraph step by step")

    capability = result["capability"]
    assert capability.route_label == "learning_coach"
    assert capability.mode == "simulation"
    assert capability.name == get_capability_for_route("learning_coach").name
    assert "simulation" in result["reply"]
    assert "not a real-world integration" in result["reply"]


def test_research_helper_capability_documents_real_world_tool_boundary() -> None:
    capability = get_capability_for_route("research_helper")

    assert capability.mode == "real_world"
    assert "web_search" in " ".join(capability.tools)
    assert capability.side_effects
