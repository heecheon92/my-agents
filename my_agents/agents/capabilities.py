"""Route capability metadata for the production assistant graph."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from my_agents.schemas import RouteLabel


class AgentCapability(BaseModel):
    """Describe what backs a route without implying a separate agent executed."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    route_label: RouteLabel
    purpose: str = Field(min_length=1)
    tools: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()

    def guidance_text(self) -> str:
        """Return concise provider guidance for honest route disclosure."""
        tools = _format_tuple(self.tools)
        side_effects = _format_tuple(self.side_effects)
        data_sources = _format_tuple(self.data_sources)
        return (
            f"Capability name: {self.name}\n"
            f"Capability purpose: {self.purpose}\n"
            f"Capability tools: {tools}\n"
            f"Capability data sources: {data_sources}\n"
            f"Capability side effects: {side_effects}"
        )


_CAPABILITIES_BY_ROUTE: dict[RouteLabel, AgentCapability] = {
    "general_assistant": AgentCapability(
        name="general_assistant_router",
        route_label="general_assistant",
        purpose=(
            "General assistant/router response path that provides practical replies "
            "through the configured response provider."
        ),
        tools=(
            "ChatOpenAI response provider when OpenAI mode is enabled",
            "OpenAI hosted web_search available in OpenAI mode for current or "
            "source-backed requests",
        ),
        data_sources=(
            "current chat messages",
            "web search results when OpenAI mode is enabled and the model calls web_search",
        ),
        side_effects=(
            "OpenAI API call when OpenAI mode is enabled",
            "hosted web search request in OpenAI mode when the model calls web_search",
        ),
    ),
    "research_helper": AgentCapability(
        name="research_helper_with_hosted_search",
        route_label="research_helper",
        purpose=(
            "Source-oriented response path that may use OpenAI hosted web search in OpenAI mode."
        ),
        tools=("OpenAI hosted web_search in OpenAI mode",),
        data_sources=("current chat messages", "web search results when OpenAI mode is enabled"),
        side_effects=(
            "OpenAI API call when OpenAI mode is enabled",
            "hosted web search request in OpenAI mode",
        ),
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
