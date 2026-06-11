"""Health-check routes for the backend service."""

from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends

from my_agents.settings import Settings, get_settings

health_router = APIRouter(tags=["health"])


class HealthFrontendDocumentConfig(TypedDict):
    """Safe document-flow hints for frontend clients."""

    upload_concurrency: int


class HealthFrontendConfig(TypedDict):
    """Safe frontend-facing runtime configuration."""

    documents: HealthFrontendDocumentConfig


class HealthResponse(TypedDict):
    """Unauthenticated health response with non-sensitive frontend hints."""

    status: str
    service: str
    version: str
    frontend_config: HealthFrontendConfig


@health_router.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Return a small unauthenticated service health payload."""
    return {
        "status": "ok",
        "service": "my-agents",
        "version": "0.1.0",
        "frontend_config": {
            "documents": {
                "upload_concurrency": settings.document_upload_concurrency,
            },
        },
    }
