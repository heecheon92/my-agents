"""FastAPI application factory and routes for the assistant backend."""

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from my_agents.graph import build_graph
from my_agents.responders import ResponseProviderConfigurationError
from my_agents.schemas import ChatRequest, ChatResponse, RouteDecision


class GraphRunner(Protocol):
    """Minimal protocol for an invokable LangGraph runner."""

    def invoke(self, input: dict) -> dict:  # noqa: A002 - matches LangGraph API name
        """Invoke the graph with a state dictionary."""
        ...


_graph_runner = build_graph()


def get_graph_runner() -> GraphRunner:
    """Return the app-wide compiled graph runner."""
    return _graph_runner


assistant_router = APIRouter(prefix="/assistant", tags=["assistant"])


@assistant_router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    graph_runner: Annotated[GraphRunner, Depends(get_graph_runner)],
) -> ChatResponse:
    """Run a validated chat request through the personal assistant graph."""
    try:
        result = graph_runner.invoke({"message": request.message, "history": request.history})
    except ResponseProviderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(
        reply=result["reply"],
        route=_coerce_route(result["route"]),
        handled_by="personal_assistant_graph",
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="my-agents", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "my-agents", "version": app.version}

    app.include_router(assistant_router)
    return app


def _coerce_route(route: RouteDecision | dict) -> RouteDecision:
    if isinstance(route, RouteDecision):
        return route
    return RouteDecision.model_validate(route)
