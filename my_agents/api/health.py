"""Health-check routes for the backend service."""

from fastapi import APIRouter

health_router = APIRouter(tags=["health"])


@health_router.get("/health")
def health() -> dict[str, str]:
    """Return a small unauthenticated service health payload."""
    return {"status": "ok", "service": "my-agents", "version": "0.1.0"}
