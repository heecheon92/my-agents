"""FastAPI application factory and route assembly for the assistant backend."""

from fastapi import FastAPI

from my_agents.api.assistant import GraphRunner, assistant_router, get_graph_runner
from my_agents.api.auth import auth_router
from my_agents.api.conversations import conversations_router
from my_agents.api.documents import documents_router
from my_agents.api.groups import groups_router
from my_agents.api.health import health_router
from my_agents.api.knowledge_bases import knowledge_bases_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="my-agents", version="0.1.0")
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(conversations_router)
    app.include_router(groups_router)
    app.include_router(knowledge_bases_router)
    app.include_router(documents_router)
    app.include_router(assistant_router)
    return app


__all__ = ["GraphRunner", "create_app", "get_graph_runner"]
