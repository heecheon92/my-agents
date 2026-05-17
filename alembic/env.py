"""Alembic environment for my-agents service schema migrations."""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import_all_models()
target_metadata = Base.metadata


def _database_url() -> str:
    """Return the migration database URL without requiring OpenAI settings."""
    env_database_url = os.environ.get("MY_AGENTS_DATABASE_URL")
    if env_database_url and env_database_url.strip():
        return env_database_url.strip()
    return _dotenv_database_url() or config.get_main_option(
        "sqlalchemy.url",
        "sqlite+pysqlite:///:memory:",
    )


def _dotenv_database_url() -> str | None:
    """Read MY_AGENTS_DATABASE_URL from a local .env without loading unrelated settings."""
    env_path = Path(config.config_file_name or "alembic.ini").parent / ".env"
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "MY_AGENTS_DATABASE_URL":
            return value.strip().strip("'\"") or None
    return None


def run_migrations_offline() -> None:
    """Run migrations in offline SQL-emission mode."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
