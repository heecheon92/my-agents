"""SQLAlchemy database boundary for app-owned service state."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from my_agents.diagnostics import deploy_log, safe_database_url_summary
from my_agents.settings import Settings, get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models owned by this backend."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache(maxsize=8)
def _engine_for_url(database_url: str) -> Engine:
    deploy_log("database.engine.create", **safe_database_url_summary(database_url))
    kwargs = _engine_kwargs_for_url(database_url)
    return create_engine(database_url, **kwargs)


def _engine_kwargs_for_url(database_url: str) -> dict:
    """Return SQLAlchemy engine kwargs for this backend's supported database URLs."""
    kwargs: dict = {"future": True}
    if database_url == "sqlite+pysqlite:///:memory:":
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    elif database_url.startswith("postgresql"):
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 300
    return kwargs


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
        deploy_log(
            "database.initialize.skip_already_initialized",
            **safe_database_url_summary(database_url),
        )
        return
    from my_agents.persistence.models import import_all_models

    deploy_log(
        "database.initialize.start",
        auto_create_tables=settings.should_auto_create_tables(),
        **safe_database_url_summary(database_url),
    )
    import_all_models()

    if settings.should_auto_create_tables():
        deploy_log(
            "database.initialize.create_all.start",
            **safe_database_url_summary(database_url),
        )
        Base.metadata.create_all(_engine_for_url(database_url))
        deploy_log(
            "database.initialize.create_all.completed",
            **safe_database_url_summary(database_url),
        )
    _initialized_urls.add(database_url)
    deploy_log("database.initialize.completed", **safe_database_url_summary(database_url))


def get_database_session() -> Generator[Session]:
    """Yield a request-scoped SQLAlchemy session."""
    settings = get_settings()
    deploy_log("database.session.requested", **safe_database_url_summary(settings.database_url))
    initialize_database(settings)
    session = _sessionmaker_for_url(settings.database_url)()
    deploy_log("database.session.opened", **safe_database_url_summary(settings.database_url))
    try:
        yield session
    finally:
        deploy_log("database.session.closed", **safe_database_url_summary(settings.database_url))
        session.close()


def reset_database_caches() -> None:
    """Clear database caches for tests that change database URLs."""
    _initialized_urls.clear()
    _sessionmaker_for_url.cache_clear()
    _engine_for_url.cache_clear()
