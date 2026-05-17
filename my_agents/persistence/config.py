"""Persistence configuration helpers.

This module keeps database-related settings small and explicit while SQLAlchemy
owns runtime sessions and Alembic owns production-like schema migration.
"""

from __future__ import annotations

from dataclasses import dataclass

from my_agents.settings import Settings


@dataclass(frozen=True)
class PersistenceConfig:
    """Database-related runtime settings normalized for persistence modules."""

    database_url: str
    test_database_url: str | None
    auto_create_tables: bool

    @property
    def has_external_test_database(self) -> bool:
        """Return whether optional external database smoke tests can run."""
        return self.test_database_url is not None


def get_persistence_config(settings: Settings) -> PersistenceConfig:
    """Build persistence config from application settings."""
    return PersistenceConfig(
        database_url=settings.database_url,
        test_database_url=settings.test_database_url,
        auto_create_tables=settings.should_auto_create_tables(),
    )
