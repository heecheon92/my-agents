"""FastAPI application factory and route assembly for the assistant backend."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from rich.logging import RichHandler

from my_agents.api.assistant import GraphRunner, assistant_router, get_graph_runner
from my_agents.api.auth import auth_router
from my_agents.api.conversations import conversations_router
from my_agents.api.documents import documents_router
from my_agents.api.groups import groups_router
from my_agents.api.health import health_router
from my_agents.api.knowledge_bases import knowledge_bases_router
from my_agents.settings import Settings, get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    configure_operational_logging()
    configure_debug_logging(settings)
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


def configure_operational_logging() -> None:
    """Enable non-sensitive service lifecycle logs in hosted runtimes."""
    logging.getLogger("my_agents.api.auth").setLevel(logging.INFO)
    logging.getLogger("my_agents.auth.email").setLevel(logging.INFO)


def configure_debug_logging(settings: Settings) -> None:
    """Enable sensitive rich debug prints only for explicit debug sessions."""
    if not settings.debug_knowledge_context_logging:
        return
    _configure_rich_debug_logging()
    logging.getLogger("my_agents.api.conversations.retrieval_context").setLevel(logging.DEBUG)
    logging.getLogger("my_agents.agents.context_forge.debug").setLevel(logging.DEBUG)
    logging.getLogger("my_agents.api.documents").setLevel(logging.INFO)
    logging.getLogger("my_agents.knowledge.uploads").setLevel(logging.INFO)
    logging.getLogger("my_agents.knowledge.pdf_uploads").setLevel(logging.DEBUG)
    logging.getLogger("my_agents.knowledge.extraction").setLevel(logging.INFO)


def _configure_rich_debug_logging() -> None:
    """Install a Rich console handler for explicit local debug sessions."""
    root_logger = logging.getLogger()
    if any(
        getattr(handler, "name", None) == "my_agents_rich_debug" for handler in root_logger.handlers
    ):
        return
    handler = RichHandler(
        rich_tracebacks=True,
        markup=True,
        show_time=True,
        show_level=True,
        show_path=False,
    )
    handler.set_name("my_agents_rich_debug")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(min(root_logger.level or logging.INFO, logging.INFO))


__all__ = ["GraphRunner", "create_app", "get_graph_runner"]
