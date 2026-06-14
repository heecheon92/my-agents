"""Alembic database migration operator script tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import migrate_database as migrate_database_script

LATEST_REVISION = "20260614_0026"


def _env_file(tmp_path: Path, database_url: str) -> Path:
    env_file = tmp_path / "migration.env"
    env_file.write_text(
        f"MY_AGENTS_RESPONSE_MODE=deterministic\nMY_AGENTS_DATABASE_URL={database_url}\n",
        encoding="utf-8",
    )
    return env_file


def test_migrate_database_status_does_not_upgrade(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "status.sqlite3"
    env_file = _env_file(tmp_path, f"sqlite+pysqlite:///{database_path}")

    exit_code = migrate_database_script.main(["--env-file", str(env_file)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "DATABASE MIGRATION" in captured.out
    assert "Database migration status" in captured.out
    assert f"database_name={database_path.name}" in captured.out
    assert "current_before=" in captured.out
    assert f"heads={LATEST_REVISION}" in captured.out
    assert "upgraded=False" in captured.out
    assert "--upgrade --confirm-upgrade --database-name" in captured.out


def test_migrate_database_upgrade_requires_confirmation(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "needs-confirmation.sqlite3"
    env_file = _env_file(tmp_path, f"sqlite+pysqlite:///{database_path}")

    exit_code = migrate_database_script.main(
        [
            "--env-file",
            str(env_file),
            "--upgrade",
            "--database-name",
            database_path.name,
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "without --confirm-upgrade" in captured.err


def test_migrate_database_upgrade_requires_exact_database_name(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "exact-name.sqlite3"
    env_file = _env_file(tmp_path, f"sqlite+pysqlite:///{database_path}")

    exit_code = migrate_database_script.main(
        [
            "--env-file",
            str(env_file),
            "--upgrade",
            "--confirm-upgrade",
            "--database-name",
            "wrong.sqlite3",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "does not match selected database" in captured.err


def test_migrate_database_upgrade_refuses_remote_postgres_without_allow_flag(
    tmp_path: Path,
    capsys,
) -> None:
    env_file = _env_file(
        tmp_path,
        "postgresql+psycopg://app:secret@db.example.com/my_agents_prod?sslmode=require",
    )

    exit_code = migrate_database_script.main(
        [
            "--env-file",
            str(env_file),
            "--upgrade",
            "--confirm-upgrade",
            "--database-name",
            "my_agents_prod",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "non-local Postgres" in captured.err
    assert "secret" not in captured.out
    assert "secret" not in captured.err


def test_migrate_database_upgrade_applies_head_to_sqlite(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    env_file = _env_file(tmp_path, f"sqlite+pysqlite:///{database_path}")

    exit_code = migrate_database_script.main(
        [
            "--env-file",
            str(env_file),
            "--upgrade",
            "--confirm-upgrade",
            "--database-name",
            database_path.name,
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Database migration applied" in captured.out
    assert "upgraded=True" in captured.out
    assert f"current_after={LATEST_REVISION}" in captured.out
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()[0]
    assert revision == LATEST_REVISION
