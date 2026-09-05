"""Process-scoped LangGraph Postgres persistence resources."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.store.base import BaseStore
from langgraph.store.postgres import PostgresStore
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy.engine import make_url

from my_agents.knowledge.embeddings import EmbeddingProvider, build_embedding_provider
from my_agents.settings import Settings

_CHECKPOINT_ALLOWED_MSGPACK_MODULES = (
    ("my_agents.schemas", "RouteDecision"),
    ("my_agents.agents.capabilities", "AgentCapability"),
    (
        "my_agents.agents.general_assistant.retrieval_gate",
        "RetrievalSourceDecision",
    ),
)


@dataclass
class LangGraphPersistenceResources:
    """Open process-scoped resources used to compile the product graph."""

    checkpointer: BaseCheckpointSaver | None = None
    store: BaseStore | None = None
    pool: ConnectionPool | None = None

    def close(self) -> None:
        """Close the shared Psycopg pool when the FastAPI lifespan ends."""
        if self.pool is not None:
            self.pool.close()


def open_langgraph_persistence(settings: Settings) -> LangGraphPersistenceResources:
    """Open the configured Postgres saver/store without running schema setup."""
    postgres_available = make_url(settings.database_url).get_backend_name() == "postgresql"
    if not postgres_available:
        return LangGraphPersistenceResources()

    pool = ConnectionPool(
        conninfo=postgres_dsn(settings.database_url),
        min_size=1,
        max_size=3,
        open=False,
        # Replace server-disconnected idle connections before checkpoint/store I/O.
        # Recovery is bounded by the pool timeout and never replays graph work.
        check=ConnectionPool.check_connection,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    pool.open(wait=True)
    checkpointer: BaseCheckpointSaver | None = None
    store: BaseStore | None = None
    checkpointer = PostgresSaver(
        pool,
        serde=checkpoint_serializer(),
    )
    if postgres_available:
        embedding_provider = build_embedding_provider(settings)
        embedding_dimensions = (
            embedding_provider.dimensions or settings.memory_store_embedding_dimensions
        )
        store = PostgresStore(
            pool,
            index={
                "dims": embedding_dimensions,
                "embed": _StoreEmbeddings(embedding_provider),
                "fields": ["content"],
            },
        )
    return LangGraphPersistenceResources(
        checkpointer=checkpointer,
        store=store,
        pool=pool,
    )


def setup_langgraph_persistence(settings: Settings) -> None:
    """Create or migrate framework-owned Postgres saver/store tables."""
    resources = open_langgraph_persistence(settings)
    try:
        if resources.checkpointer is not None:
            setup = getattr(resources.checkpointer, "setup", None)
            if callable(setup):
                setup()
        if resources.store is not None:
            setup = getattr(resources.store, "setup", None)
            if callable(setup):
                setup()
    finally:
        resources.close()


def postgres_dsn(database_url: str) -> str:
    """Return a Psycopg-compatible DSN from the SQLAlchemy application URL."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("LangGraph persistence requires a PostgreSQL database URL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def checkpoint_serializer() -> JsonPlusSerializer:
    """Allow only built-in safe types plus the graph's three explicit value objects."""
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=_CHECKPOINT_ALLOWED_MSGPACK_MODULES,
    )


class _StoreEmbeddings:
    """Adapt the existing embedding boundary to LangGraph Store's interface."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._provider.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._provider.embed_query(text)

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        return self._provider.embed_documents(list(texts))


__all__ = [
    "LangGraphPersistenceResources",
    "open_langgraph_persistence",
    "checkpoint_serializer",
    "postgres_dsn",
    "setup_langgraph_persistence",
]
