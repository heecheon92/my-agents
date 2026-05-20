"""FastAPI application factory and route assembly for the assistant backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from my_agents.api.assistant import GraphRunner, assistant_router, get_graph_runner
from my_agents.api.auth import auth_router
from my_agents.api.conversations import conversations_router
from my_agents.api.documents import documents_router
from my_agents.api.groups import groups_router
from my_agents.api.health import health_router
from my_agents.api.knowledge_bases import knowledge_bases_router
from my_agents.settings import get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title="my-agents", version="0.1.0")
    cors_allowed_origins = settings.cors_allowed_origin_list()
    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_allowed_origins),
            allow_credentials=True,
            allow_methods=["DELETE", "GET", "OPTIONS", "PATCH", "POST"],
            allow_headers=["Content-Type", settings.csrf_header_name],
        )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(conversations_router)
    app.include_router(groups_router)
    app.include_router(knowledge_bases_router)
    app.include_router(documents_router)
    app.include_router(assistant_router)
    return app


__all__ = ["GraphRunner", "create_app", "get_graph_runner"]
