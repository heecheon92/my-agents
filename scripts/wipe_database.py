"""Dangerous database wipe helper for intentionally rebuilding an environment."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from my_agents.diagnostics import safe_database_url_summary
from my_agents.persistence.database import reset_database_caches
from my_agents.settings import Settings
from scripts.ops_common import add_env_arguments, resolve_env_file

LOCAL_POSTGRES_HOSTS = {"localhost", "127.0.0.1", "::1"}
SUPPORTED_DIALECTS = {"sqlite", "postgresql"}
WIPE_WARNING = (
    "!!! DANGER: DATABASE WIPE PERMANENTLY DELETES ALL APP DATA AND SCHEMA "
    "IN THE SELECTED DATABASE. BACK UP THE DATABASE FIRST, STOP RUNNING APP "
    "PROCESSES, AND VERIFY THE ENV FILE AND DATABASE NAME BEFORE EXECUTING."
)


@dataclass(frozen=True)
class DatabaseWipeResult:
    """Printable result for a database wipe plan or execution."""

    dialect: str
    database_name: str
    target: str
    dry_run: bool
    wiped: bool
    object_count: int


def wipe_database(
    *,
    settings: Settings,
    confirm_wipe: bool = False,
    expected_database_name: str | None = None,
    allow_remote_postgres: bool = False,
    dry_run: bool = True,
) -> DatabaseWipeResult:
    """Fully remove app database contents for the selected SQLite/Postgres URL.

    SQLite file URLs are wiped by deleting the database file. Postgres URLs are wiped by
    dropping and recreating the selected database's `public` schema.
    """
    reset_database_caches()
    url = make_url(settings.database_url)
    dialect = url.get_backend_name()
    if dialect not in SUPPORTED_DIALECTS:
        raise ValueError(f"unsupported database dialect for wipe: {dialect}")

    if dialect == "sqlite":
        return _wipe_sqlite_database(
            database_url=settings.database_url,
            confirm_wipe=confirm_wipe,
            expected_database_name=expected_database_name,
            dry_run=dry_run,
        )
    return _wipe_postgres_database(
        database_url=settings.database_url,
        confirm_wipe=confirm_wipe,
        expected_database_name=expected_database_name,
        allow_remote_postgres=allow_remote_postgres,
        dry_run=dry_run,
    )


def _wipe_sqlite_database(
    *,
    database_url: str,
    confirm_wipe: bool,
    expected_database_name: str | None,
    dry_run: bool,
) -> DatabaseWipeResult:
    url = make_url(database_url)
    if not url.database or url.database == ":memory:":
        raise ValueError("refusing to wipe in-memory SQLite database")
    database_path = Path(url.database).expanduser().resolve()
    database_name = database_path.name
    _require_destructive_confirmation(
        confirm_wipe=confirm_wipe,
        expected_database_name=expected_database_name,
        actual_database_name=database_name,
        dry_run=dry_run,
    )
    object_count = 1 if database_path.exists() else 0
    if not dry_run and database_path.exists():
        database_path.unlink()
    return DatabaseWipeResult(
        dialect="sqlite",
        database_name=database_name,
        target=str(database_path),
        dry_run=dry_run,
        wiped=not dry_run,
        object_count=object_count,
    )


def _wipe_postgres_database(
    *,
    database_url: str,
    confirm_wipe: bool,
    expected_database_name: str | None,
    allow_remote_postgres: bool,
    dry_run: bool,
) -> DatabaseWipeResult:
    url = make_url(database_url)
    if not url.database:
        raise ValueError("refusing to wipe Postgres URL without a database name")
    database_name = url.database
    if not _is_local_postgres_url(url) and not allow_remote_postgres:
        raise ValueError("refusing to wipe non-local Postgres without --allow-remote-postgres")
    _require_destructive_confirmation(
        confirm_wipe=confirm_wipe,
        expected_database_name=expected_database_name,
        actual_database_name=database_name,
        dry_run=dry_run,
    )

    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            object_count = _postgres_public_object_count(connection)
        if not dry_run:
            with engine.begin() as connection:
                connection.execute(text("drop schema if exists public cascade"))
                connection.execute(text("create schema public"))
                connection.execute(text("grant all on schema public to public"))
    finally:
        engine.dispose()

    return DatabaseWipeResult(
        dialect="postgresql",
        database_name=database_name,
        target=_masked_target(database_url),
        dry_run=dry_run,
        wiped=not dry_run,
        object_count=object_count,
    )


def _postgres_public_object_count(connection: sa.Connection) -> int:
    inspector = inspect(connection)
    table_count = len(inspector.get_table_names(schema="public"))
    view_count = len(inspector.get_view_names(schema="public"))
    sequence_count = connection.execute(
        text(
            """
            select count(*)
            from information_schema.sequences
            where sequence_schema = 'public'
            """
        )
    ).scalar_one()
    return table_count + view_count + int(sequence_count)


def _is_local_postgres_url(url: sa.engine.URL) -> bool:
    return (url.host or "").casefold() in LOCAL_POSTGRES_HOSTS


def _require_destructive_confirmation(
    *,
    confirm_wipe: bool,
    expected_database_name: str | None,
    actual_database_name: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    if not confirm_wipe:
        raise ValueError("refusing to wipe without --confirm-wipe")
    if expected_database_name != actual_database_name:
        raise ValueError(
            "refusing to wipe because --database-name does not match selected database "
            f"({actual_database_name})"
        )


def _masked_target(database_url: str) -> str:
    summary = safe_database_url_summary(database_url)
    scheme = summary.get("db_scheme", "unknown")
    host = summary.get("db_host", "unknown")
    name = summary.get("db_name", "unknown")
    return f"{scheme}://{host}/{name}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fully wipe a selected SQLite/Postgres database. Dry-run by default; "
            "requires --confirm-wipe and --database-name to delete anything."
        )
    )
    add_env_arguments(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually wipe the selected database. Without this flag, only print the plan.",
    )
    parser.add_argument(
        "--confirm-wipe",
        action="store_true",
        help="Required with --execute to acknowledge destructive data loss.",
    )
    parser.add_argument(
        "--database-name",
        default=None,
        help=(
            "Required with --execute. Must exactly match the selected SQLite filename "
            "or Postgres database name."
        ),
    )
    parser.add_argument(
        "--allow-remote-postgres",
        action="store_true",
        help="Allow a non-local Postgres host to be wiped. Still requires confirmations.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    env_file = resolve_env_file(profile=args.env, env_file=args.env_file)
    if not env_file.is_file():
        print(f"error: env file does not exist: {env_file}", file=sys.stderr)
        return 1
    print(WIPE_WARNING)
    settings = Settings(_env_file=env_file)
    dry_run = not args.execute
    try:
        result = wipe_database(
            settings=settings,
            confirm_wipe=args.confirm_wipe,
            expected_database_name=args.database_name,
            allow_remote_postgres=args.allow_remote_postgres,
            dry_run=dry_run,
        )
    except (ValueError, SQLAlchemyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    action = "Database wipe plan" if result.dry_run else "Database wiped"
    print(action)
    print(f"env_file={env_file}")
    print(f"dialect={result.dialect}")
    print(f"database_name={result.database_name}")
    print(f"target={result.target}")
    print(f"object_count={result.object_count}")
    print(f"wiped={result.wiped}")
    if result.dry_run:
        print("next_step=rerun with --execute --confirm-wipe --database-name <database_name>")
    else:
        print("next_step=run uv run alembic upgrade head before starting the app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
