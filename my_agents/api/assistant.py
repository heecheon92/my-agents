"""Legacy assistant routes for the current v0 graph-backed chat surface."""

from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.store.base import BaseStore

from my_agents.agents.general_assistant.graph import build_graph, build_legacy_chat_graph
from my_agents.agents.general_assistant.responders import ResponseProviderConfigurationError
from my_agents.schemas import ChatRequest, ChatResponse, RouteDecision


class GraphRunner(Protocol):
    """Minimal protocol for an invokable LangGraph runner."""

    def invoke(self, input: dict, **kwargs: Any) -> dict:  # noqa: A002 - matches LangGraph API
        """Invoke the graph with a state dictionary."""
        ...

    def stream(self, input: dict, **kwargs: Any):  # noqa: A002 - matches LangGraph API name
        """Stream graph events for a state dictionary when the runner supports it."""
        ...


_graph_runner = build_graph()
_legacy_chat_graph_runner = build_legacy_chat_graph()


def get_graph_runner(request: Request) -> GraphRunner:
    """Return the lifespan-owned retrieval-enabled product graph runner."""
    runner = getattr(request.app.state, "graph_runner", None)
    return runner or _graph_runner


def get_memory_store(request: Request) -> BaseStore | None:
    """Return the lifespan-owned LangGraph Store when the feature is enabled."""
    return getattr(request.app.state, "langgraph_store", None)


def get_legacy_chat_graph_runner() -> GraphRunner:
    """Return the unauthenticated legacy/dev chat graph runner."""
    return _legacy_chat_graph_runner


assistant_router = APIRouter(prefix="/assistant", tags=["assistant"])


@assistant_router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    graph_runner: Annotated[GraphRunner, Depends(get_legacy_chat_graph_runner)],
) -> ChatResponse:
    """Run a validated chat request through the legacy personal assistant graph.

    This endpoint remains the v0 deterministic/OpenAI smoke surface. The product
    service plan treats it as a legacy/dev route once authenticated conversation-run
    endpoints exist, so it must not become a permission bypass for future KB access.
    """
    try:
        result = graph_runner.invoke({"messages": _messages_from_request(request)})
    except ResponseProviderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(
        reply=result["reply"],
        route=_coerce_route(result["route"]),
        handled_by="personal_assistant_graph",
    )


def _coerce_route(route: RouteDecision | dict) -> RouteDecision:
    if isinstance(route, RouteDecision):
        return route
    return RouteDecision.model_validate(route)


def _messages_from_request(request: ChatRequest) -> list[BaseMessage]:
    """Convert public JSON request shape into LangChain message objects."""
    messages: list[BaseMessage] = []
    for item in request.history:
        if item["role"] == "assistant":
            messages.append(AIMessage(content=item["content"]))
        else:
            messages.append(HumanMessage(content=item["content"]))
    messages.append(HumanMessage(content=request.message))
    return messages
