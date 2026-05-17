"""SQLAlchemy database boundary for app-owned service state."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from my_agents.settings import Settings, get_settings


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models owned by this backend."""


@lru_cache(maxsize=8)
def _engine_for_url(database_url: str) -> Engine:
    kwargs: dict = {"future": True}
    if database_url == "sqlite+pysqlite:///:memory:":
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


@lru_cache(maxsize=8)
def _sessionmaker_for_url(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=_engine_for_url(database_url), autoflush=False, expire_on_commit=False)


_initialized_urls: set[str] = set()


def initialize_database(settings: Settings) -> None:
    """Create currently implemented tables for local/dev use.

    Alembic migrations are the planned production path. This create-all hook keeps the
    first auth/session milestone runnable and deterministic while migrations mature.
    """
    database_url = settings.database_url
    if database_url in _initialized_urls:
        return
    # Import model modules so their tables are registered on Base.metadata.
    import my_agents.auth.models  # noqa: F401
    import my_agents.conversations.models  # noqa: F401
    import my_agents.groups.models  # noqa: F401
    import my_agents.knowledge.models  # noqa: F401

    Base.metadata.create_all(_engine_for_url(database_url))
    _initialized_urls.add(database_url)


def get_database_session() -> Generator[Session]:
    """Yield a request-scoped SQLAlchemy session."""
    settings = get_settings()
    initialize_database(settings)
    session = _sessionmaker_for_url(settings.database_url)()
    try:
        yield session
    finally:
        session.close()


def reset_database_caches() -> None:
    """Clear database caches for tests that change database URLs."""
    _initialized_urls.clear()
    _sessionmaker_for_url.cache_clear()
    _engine_for_url.cache_clear()
