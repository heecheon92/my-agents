"""Prometheus timing metrics tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from my_agents.api import create_app
from my_agents.knowledge.embeddings import DeterministicEmbeddingProvider
from my_agents.observability.metrics import prometheus_payload
from my_agents.settings import get_settings


def test_metrics_endpoint_is_disabled_by_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.delenv("MY_AGENTS_METRICS_ENABLED", raising=False)
    get_settings.cache_clear()

    client = TestClient(create_app())

    assert client.get("/metrics").status_code == 404


def test_metrics_endpoint_exposes_request_duration_when_enabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_METRICS_ENABLED", "true")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/health").status_code == 200

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "my_agents_http_request_duration_seconds_bucket" in response.text
    assert 'route="/health"' in response.text
    assert 'status_code="200"' in response.text


def test_request_metrics_do_not_label_unmatched_raw_paths(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_METRICS_ENABLED", "true")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/missing/private-document-123").status_code == 404

    response = client.get("/metrics")

    assert response.status_code == 200
    assert 'route="unmatched"' in response.text
    assert "private-document-123" not in response.text


def test_embedding_calls_record_duration_when_metrics_are_enabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_METRICS_ENABLED", "true")
    get_settings.cache_clear()

    DeterministicEmbeddingProvider().embed_query("measure retrieval timing")
    payload, _ = prometheus_payload()

    text = payload.decode("utf-8")
    assert "my_agents_embedding_duration_seconds_bucket" in text
    assert 'provider="deterministic"' in text
    assert 'operation="query"' in text
