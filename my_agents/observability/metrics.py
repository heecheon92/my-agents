"""Prometheus metrics for request and retrieval timing.

The metrics surface is intentionally opt-in through ``MY_AGENTS_METRICS_ENABLED``.
When disabled, the timing helpers become no-ops so local tests and public demos do
not pay unnecessary overhead or expose an operational endpoint by accident.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest

from my_agents.settings import get_settings

_LocalTimingObserver = Callable[[str, float], None]
_LOCAL_TIMING_OBSERVER: ContextVar[_LocalTimingObserver | None] = ContextVar(
    "my_agents_local_timing_observer",
    default=None,
)

_REQUEST_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
)
_INTERNAL_BUCKETS = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "my_agents_http_request_duration_seconds",
    "HTTP request duration by method, normalized route, and status code.",
    ("method", "route", "status_code"),
    buckets=_REQUEST_BUCKETS,
)

CONVERSATION_RUN_DURATION_SECONDS = Histogram(
    "my_agents_conversation_run_duration_seconds",
    "Conversation run duration by endpoint mode, outcome, retrieval route, and answer mode.",
    ("mode", "outcome", "retrieval_route", "answer_mode"),
    buckets=_REQUEST_BUCKETS,
)

CONTEXT_FORGE_DURATION_SECONDS = Histogram(
    "my_agents_context_forge_duration_seconds",
    "ContextForge retrieval orchestration duration by retrieval route and answer mode.",
    ("retrieval_route", "answer_mode"),
    buckets=_INTERNAL_BUCKETS,
)

RETRIEVAL_PHASE_DURATION_SECONDS = Histogram(
    "my_agents_retrieval_phase_duration_seconds",
    "Internal retrieval phase duration. Phase timings are spans and are not additive.",
    ("phase",),
    buckets=_INTERNAL_BUCKETS,
)

EMBEDDING_DURATION_SECONDS = Histogram(
    "my_agents_embedding_duration_seconds",
    "Embedding provider call duration by provider, model, and operation.",
    ("provider", "model", "operation"),
    buckets=_INTERNAL_BUCKETS,
)

RERANKER_DURATION_SECONDS = Histogram(
    "my_agents_reranker_duration_seconds",
    "ContextForge reranker duration by reranker implementation.",
    ("reranker",),
    buckets=_INTERNAL_BUCKETS,
)

GRAPH_INVOCATION_DURATION_SECONDS = Histogram(
    "my_agents_graph_invocation_duration_seconds",
    "Assistant graph invocation duration by invocation mode.",
    ("mode",),
    buckets=_REQUEST_BUCKETS,
)

LANGGRAPH_PERSISTENCE_OPERATIONS_TOTAL = Counter(
    "my_agents_langgraph_persistence_operations_total",
    "LangGraph checkpointer and Store operations by operation and outcome.",
    ("operation", "outcome"),
)


def metrics_enabled() -> bool:
    """Return whether metrics should be recorded and exposed in this process."""
    try:
        return get_settings().metrics_enabled
    except Exception:
        return False


def prometheus_payload() -> tuple[bytes, str]:
    """Return the current Prometheus exposition payload and content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def observe_http_request(
    *,
    method: str,
    route: str,
    status_code: int | str,
    duration_seconds: float,
) -> None:
    if not metrics_enabled():
        return
    HTTP_REQUEST_DURATION_SECONDS.labels(
        method=_label(method),
        route=_label(route),
        status_code=str(status_code),
    ).observe(duration_seconds)


def observe_conversation_run(
    *,
    mode: str,
    outcome: str,
    retrieval_route: str,
    answer_mode: str,
    duration_seconds: float,
) -> None:
    if not metrics_enabled():
        return
    CONVERSATION_RUN_DURATION_SECONDS.labels(
        mode=_label(mode),
        outcome=_label(outcome),
        retrieval_route=_label(retrieval_route),
        answer_mode=_label(answer_mode),
    ).observe(duration_seconds)


def observe_context_forge(
    *,
    retrieval_route: str,
    answer_mode: str,
    duration_seconds: float,
) -> None:
    if not metrics_enabled():
        return
    CONTEXT_FORGE_DURATION_SECONDS.labels(
        retrieval_route=_label(retrieval_route),
        answer_mode=_label(answer_mode),
    ).observe(duration_seconds)


def record_langgraph_persistence_operation(*, operation: str, outcome: str) -> None:
    if not metrics_enabled():
        return
    LANGGRAPH_PERSISTENCE_OPERATIONS_TOTAL.labels(
        operation=_label(operation),
        outcome=_label(outcome),
    ).inc()


@contextmanager
def track_retrieval_phase(phase: str) -> Iterator[None]:
    """Record one retrieval-internal timing span when metrics or local tracing are enabled."""
    observer = _LOCAL_TIMING_OBSERVER.get()
    metrics_are_enabled = metrics_enabled()
    if not metrics_are_enabled and observer is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        duration_seconds = perf_counter() - started
        if metrics_are_enabled:
            RETRIEVAL_PHASE_DURATION_SECONDS.labels(phase=_label(phase)).observe(duration_seconds)
        if observer is not None:
            observer(_label(phase), duration_seconds)


@contextmanager
def track_embedding_call(
    *,
    provider: str,
    model: str,
    operation: str,
) -> Iterator[None]:
    """Record one embedding provider call."""
    observer = _LOCAL_TIMING_OBSERVER.get()
    metrics_are_enabled = metrics_enabled()
    if not metrics_are_enabled and observer is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        safe_provider = _label(provider)
        safe_model = _label(model)
        safe_operation = _label(operation)
        duration_seconds = perf_counter() - started
        if metrics_are_enabled:
            EMBEDDING_DURATION_SECONDS.labels(
                provider=safe_provider,
                model=safe_model,
                operation=safe_operation,
            ).observe(duration_seconds)
        if observer is not None:
            observer(
                ".".join(("embedding", safe_operation, safe_provider)),
                duration_seconds,
            )


@contextmanager
def track_reranker(reranker: str) -> Iterator[None]:
    """Record one ContextForge reranker invocation."""
    if not metrics_enabled():
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        RERANKER_DURATION_SECONDS.labels(reranker=_label(reranker)).observe(
            perf_counter() - started
        )


@contextmanager
def track_graph_invocation(mode: str) -> Iterator[None]:
    """Record one assistant graph invocation span."""
    if not metrics_enabled():
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        GRAPH_INVOCATION_DURATION_SECONDS.labels(mode=_label(mode)).observe(
            perf_counter() - started
        )


@contextmanager
def capture_local_timing_phases(observer: _LocalTimingObserver) -> Iterator[None]:
    """Forward nested observability spans into one local human-readable trace.

    This is intentionally separate from Prometheus enablement: local debugging often needs
    one detailed run breakdown without exposing or scraping the aggregate ``/metrics`` endpoint.
    """
    token = _LOCAL_TIMING_OBSERVER.set(observer)
    try:
        yield
    finally:
        _LOCAL_TIMING_OBSERVER.reset(token)


def _label(value: object) -> str:
    label = str(value or "unknown").strip()
    return label if label else "unknown"
