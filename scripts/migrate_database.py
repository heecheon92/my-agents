"""Run Alembic migrations against a selected operator database env."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from alembic import command
from my_agents.diagnostics import safe_database_url_summary
from scripts.ops_common import add_env_arguments, resolve_env_file

LOCAL_POSTGRES_HOSTS = {"localhost", "127.0.0.1", "::1"}
MIGRATION_WARNING = (
    "!!! DATABASE MIGRATION WILL APPLY ALEMBIC SCHEMA CHANGES TO THE SELECTED "
    "DATABASE. TAKE A PROVIDER SNAPSHOT/BACKUP FIRST, STOP INCOMPATIBLE APP "
    "PROCESSES IF NEEDED, AND VERIFY THE ENV FILE AND DATABASE NAME BEFORE "
    "EXECUTING."
)


@dataclass(frozen=True)
class DatabaseMigrationResult:
    """Printable result for a migration status check or upgrade."""

    dialect: str
    database_name: str
    target: str
    current_before: tuple[str, ...]
    heads: tuple[str, ...]
    current_after: tuple[str, ...]
    upgraded: bool


def run_database_migrations(
    *,
    database_url: str,
    confirm_upgrade: bool = False,
    expected_database_name: str | None = None,
    allow_remote_postgres: bool = False,
    upgrade: bool = False,
) -> DatabaseMigrationResult:
    """Check Alembic status and optionally run ``upgrade head``.

    The selected database URL is injected only into this process for Alembic's
    env.py. The URL itself is never printed by this script.
    """
    url = make_url(database_url)
    dialect = url.get_backend_name()
    database_name = _database_name(url)
    _require_upgrade_confirmation(
        url=url,
        database_name=database_name,
        confirm_upgrade=confirm_upgrade,
        expected_database_name=expected_database_name,
        allow_remote_postgres=allow_remote_postgres,
        upgrade=upgrade,
    )

    config = _alembic_config()
    heads = tuple(ScriptDirectory.from_config(config).get_heads())
    current_before = _current_database_revisions(database_url)

    if upgrade:
        with _temporary_env("MY_AGENTS_DATABASE_URL", database_url):
            command.upgrade(config, "head")
            command.current(config, check_heads=True)

    current_after = _current_database_revisions(database_url)
    return DatabaseMigrationResult(
        dialect=dialect,
        database_name=database_name,
        target=_masked_target(database_url),
        current_before=current_before,
        heads=heads,
        current_after=current_after,
        upgraded=upgrade,
    )


def _require_upgrade_confirmation(
    *,
    url,  # noqa: ANN001 - SQLAlchemy URL type is stable but verbose here.
    database_name: str,
    confirm_upgrade: bool,
    expected_database_name: str | None,
    allow_remote_postgres: bool,
    upgrade: bool,
) -> None:
    if not upgrade:
        return
    if not confirm_upgrade:
        raise ValueError("refusing to run Alembic upgrade without --confirm-upgrade")
    if expected_database_name != database_name:
        raise ValueError(
            "refusing to run Alembic upgrade because --database-name does not match "
            f"selected database ({database_name})"
        )
    if _is_remote_postgres_url(url) and not allow_remote_postgres:
        raise ValueError(
            "refusing to run Alembic upgrade on non-local Postgres without --allow-remote-postgres"
        )


def _is_remote_postgres_url(url) -> bool:  # noqa: ANN001 - see _require_upgrade_confirmation.
    return (
        url.get_backend_name() == "postgresql"
        and (url.host or "").casefold() not in LOCAL_POSTGRES_HOSTS
    )


def _database_name(url) -> str:  # noqa: ANN001 - see _require_upgrade_confirmation.
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            return ":memory:"
        return Path(url.database).name
    if not url.database:
        return "unknown"
    return url.database


def _current_database_revisions(database_url: str) -> tuple[str, ...]:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            if not inspect(connection).has_table("alembic_version"):
                return ()
            rows = connection.execute(text("select version_num from alembic_version")).fetchall()
            return tuple(str(row[0]) for row in rows)
    finally:
        engine.dispose()


def _alembic_config() -> Config:
    repo_root = Path(__file__).resolve().parents[1]
    return Config(str(repo_root / "alembic.ini"))


def _database_url_from_env_file(env_file: Path) -> str:
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if key == "MY_AGENTS_DATABASE_URL":
            database_url = value.strip().strip("'\"")
            if database_url:
                return database_url
    raise ValueError(f"MY_AGENTS_DATABASE_URL is missing from env file: {env_file}")


@contextmanager
def _temporary_env(key: str, value: str) -> Iterator[None]:
    old_value = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if old_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_value


def _masked_target(database_url: str) -> str:
    summary = safe_database_url_summary(database_url)
    scheme = summary.get("db_scheme", "unknown")
    host = summary.get("db_host", "local")
    name = summary.get("db_name", "unknown")
    return f"{scheme}://{host}/{name}"


def _format_revisions(revisions: tuple[str, ...]) -> str:
    return ",".join(revisions) if revisions else ""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check or run Alembic upgrade head for the selected operator env."
    )
    add_env_arguments(parser)
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Actually run Alembic upgrade head. Without this flag, only print status.",
    )
    parser.add_argument(
        "--confirm-upgrade",
        action="store_true",
        help="Required with --upgrade to acknowledge production migration risk.",
    )
    parser.add_argument(
        "--database-name",
        default=None,
        help="Required with --upgrade. Must exactly match the selected database name.",
    )
    parser.add_argument(
        "--allow-remote-postgres",
        action="store_true",
        help="Allow a non-local Postgres host to be upgraded. Still requires confirmations.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    env_file = resolve_env_file(profile=args.env, env_file=args.env_file)
    if not env_file.is_file():
        print(f"error: env file does not exist: {env_file}", file=sys.stderr)
        return 1
    print(MIGRATION_WARNING)
    try:
        database_url = _database_url_from_env_file(env_file)
        result = run_database_migrations(
            database_url=database_url,
            confirm_upgrade=args.confirm_upgrade,
            expected_database_name=args.database_name,
            allow_remote_postgres=args.allow_remote_postgres,
            upgrade=args.upgrade,
        )
    except (ValueError, SQLAlchemyError, CommandError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    action = "Database migration applied" if result.upgraded else "Database migration status"
    print(action)
    print(f"env_file={env_file}")
    print(f"dialect={result.dialect}")
    print(f"database_name={result.database_name}")
    print(f"target={result.target}")
    print(f"current_before={_format_revisions(result.current_before)}")
    print(f"heads={_format_revisions(result.heads)}")
    print(f"current_after={_format_revisions(result.current_after)}")
    print(f"upgraded={result.upgraded}")
    if result.upgraded:
        print("next_step=restart or redeploy app processes, then run hosted smoke checks")
    else:
        print("next_step=rerun with --upgrade --confirm-upgrade --database-name <database_name>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
