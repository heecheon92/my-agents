"""Internal Prometheus metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Response

from my_agents.observability.metrics import prometheus_payload

metrics_router = APIRouter()


@metrics_router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Return Prometheus text exposition for opt-in operational timing metrics."""
    payload, content_type = prometheus_payload()
    return Response(content=payload, media_type=content_type)
