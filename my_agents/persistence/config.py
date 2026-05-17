"""Persistence configuration helpers.

This module is intentionally dependency-light for the service-foundation milestone.
SQLAlchemy/Alembic/pgvector integration belongs in the next persistence implementation
step after the dependency decision is explicitly accepted.
"""

from __future__ import annotations

from dataclasses import dataclass

from my_agents.settings import Settings


@dataclass(frozen=True)
class PersistenceConfig:
    """Database-related runtime settings normalized for persistence modules."""

    database_url: str
    test_database_url: str | None

    @property
    def has_external_test_database(self) -> bool:
        """Return whether optional Postgres/pgvector integration tests can run."""
        return self.test_database_url is not None


def get_persistence_config(settings: Settings) -> PersistenceConfig:
    """Build persistence config from application settings."""
    return PersistenceConfig(
        database_url=settings.database_url,
        test_database_url=settings.test_database_url,
    )
