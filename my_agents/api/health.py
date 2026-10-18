"""Health-check routes for the backend service."""

from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends

from my_agents.api.document_files import ai_ingestion_available, direct_uploads_available
from my_agents.conversations.durable_runtime import worker_ready
from my_agents.knowledge.file_schemas import (
    MULTIPART_MAX_BYTES,
    UPLOAD_EXTENSIONS,
    UPLOAD_MAX_BYTES,
)
from my_agents.settings import Settings, get_settings

health_router = APIRouter(tags=["health"])


class HealthFrontendDocumentConfig(TypedDict):
    """Safe document-flow hints for frontend clients."""

    upload_concurrency: int
    direct_uploads: bool
    max_upload_bytes: int
    multipart_max_bytes: int
    allowed_extensions: list[str]
    ai_assisted_ingestion: bool


class HealthFrontendDurableRunConfig(TypedDict):
    enabled: bool
    ready: bool


class HealthFrontendConfig(TypedDict):
    """Safe frontend-facing runtime configuration."""

    documents: HealthFrontendDocumentConfig
    durable_runs: HealthFrontendDurableRunConfig


class HealthResponse(TypedDict):
    """Unauthenticated health response with non-sensitive frontend hints."""

    status: str
    service: str
    version: str
    frontend_config: HealthFrontendConfig


@health_router.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Return a small unauthenticated service health payload."""
    result: HealthResponse = {
        "status": "ok",
        "service": "my-agents",
        "version": "0.1.0",
        "frontend_config": {
            "durable_runs": {
                "enabled": settings.durable_runs_enabled,
                "ready": worker_ready(settings),
            },
            "documents": {
                "upload_concurrency": settings.document_upload_concurrency,
                "direct_uploads": direct_uploads_available(settings),
                "max_upload_bytes": UPLOAD_MAX_BYTES,
                "multipart_max_bytes": MULTIPART_MAX_BYTES,
                "allowed_extensions": UPLOAD_EXTENSIONS,
                "ai_assisted_ingestion": ai_ingestion_available(settings),
            },
        },
    }

    return result
