"""Capability metadata for real-world and simulated agent implementations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from my_agents.schemas import RouteLabel

CapabilityMode = Literal["real_world", "simulation"]
CapabilityMaturity = Literal["toy", "prototype", "product", "production_candidate"]


class AgentCapability(BaseModel):
    """Describe what backs a route without implying a separate agent executed."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    route_label: RouteLabel
    mode: CapabilityMode
    purpose: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    maturity: CapabilityMaturity

    def guidance_text(self) -> str:
        """Return concise provider guidance for honest capability disclosure."""
        tools = _format_tuple(self.tools)
        side_effects = _format_tuple(self.side_effects)
        data_sources = _format_tuple(self.data_sources)
        return (
            f"Capability name: {self.name}\n"
            f"Capability mode: {self.mode}\n"
            f"Capability maturity: {self.maturity}\n"
            f"Capability purpose: {self.purpose}\n"
            f"Capability tools: {tools}\n"
            f"Capability data sources: {data_sources}\n"
            f"Capability side effects: {side_effects}"
        )


_CAPABILITIES_BY_ROUTE: dict[RouteLabel, AgentCapability] = {
    "general_assistant": AgentCapability(
        name="general_assistant_router",
        route_label="general_assistant",
        mode="real_world",
        purpose=(
            "General assistant/router foundation that can provide practical replies "
            "through the configured response provider."
        ),
        tools=("ChatOpenAI response provider when OpenAI mode is enabled",),
        data_sources=("current chat messages",),
        side_effects=("OpenAI API call when OpenAI mode is enabled",),
        maturity="prototype",
    ),
    "learning_coach": AgentCapability(
        name="simulated_learning_coach",
        route_label="learning_coach",
        mode="simulation",
        purpose=(
            "Learning-oriented response path for practicing study-plan and explanation patterns; "
            "not a separate production learning coach."
        ),
        data_sources=("current chat messages",),
        maturity="toy",
    ),
    "research_helper": AgentCapability(
        name="research_helper_with_hosted_search",
        route_label="research_helper",
        mode="real_world",
        purpose=(
            "Source-oriented response path that may use OpenAI hosted web search in OpenAI mode."
        ),
        tools=("OpenAI hosted web_search in OpenAI mode",),
        data_sources=("current chat messages", "web search results when OpenAI mode is enabled"),
        side_effects=(
            "OpenAI API call when OpenAI mode is enabled",
            "hosted web search request in OpenAI mode",
        ),
        maturity="prototype",
    ),
    "project_planner": AgentCapability(
        name="simulated_project_planner",
        route_label="project_planner",
        mode="simulation",
        purpose=(
            "Planning-oriented response path for practicing milestone decomposition; "
            "not a project-management system or task database integration."
        ),
        data_sources=("current chat messages",),
        maturity="toy",
    ),
    "career_helper": AgentCapability(
        name="simulated_career_helper",
        route_label="career_helper",
        mode="simulation",
        purpose=(
            "Career-material response path for practicing wording and review patterns; "
            "not an integration with job boards, recruiters, or profile databases."
        ),
        data_sources=("current chat messages",),
        maturity="toy",
    ),
}


def get_capability_for_route(route_label: RouteLabel) -> AgentCapability:
    """Return the capability metadata for a route label."""
    return _CAPABILITIES_BY_ROUTE[route_label]


def list_capabilities() -> tuple[AgentCapability, ...]:
    """Return registered capabilities in route-registration order."""
    return tuple(_CAPABILITIES_BY_ROUTE.values())


def _format_tuple(items: tuple[str, ...]) -> str:
    if not items:
        return "none"
    return "; ".join(items)
