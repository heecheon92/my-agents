"""Dangerous database wipe script tests."""

from __future__ import annotations

from pathlib import Path

from my_agents.settings import Settings
from scripts import wipe_database as wipe_database_script


def _env_file(tmp_path: Path, database_path: Path) -> Path:
    env_file = tmp_path / "wipe.env"
    env_file.write_text(
        "MY_AGENTS_RESPONSE_MODE=deterministic\n"
        f"MY_AGENTS_DATABASE_URL=sqlite+pysqlite:///{database_path}\n",
        encoding="utf-8",
    )
    return env_file


def test_wipe_database_dry_run_warns_and_keeps_sqlite_file(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "demo.sqlite3"
    database_path.write_text("legacy database", encoding="utf-8")
    env_file = _env_file(tmp_path, database_path)

    exit_code = wipe_database_script.main(["--env-file", str(env_file)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert database_path.exists()
    assert "!!! DANGER: DATABASE WIPE" in captured.out
    assert "Database wipe plan" in captured.out
    assert "wiped=False" in captured.out
    assert "--execute --confirm-wipe --database-name" in captured.out


def test_wipe_database_execute_requires_confirmation(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "demo.sqlite3"
    database_path.write_text("legacy database", encoding="utf-8")
    env_file = _env_file(tmp_path, database_path)

    exit_code = wipe_database_script.main(["--env-file", str(env_file), "--execute"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert database_path.exists()
    assert "refusing to wipe without --confirm-wipe" in captured.err


def test_wipe_database_execute_requires_exact_database_name(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "demo.sqlite3"
    database_path.write_text("legacy database", encoding="utf-8")
    env_file = _env_file(tmp_path, database_path)

    exit_code = wipe_database_script.main(
        [
            "--env-file",
            str(env_file),
            "--execute",
            "--confirm-wipe",
            "--database-name",
            "wrong.sqlite3",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert database_path.exists()
    assert "does not match selected database" in captured.err


def test_wipe_database_execute_deletes_sqlite_file(
    tmp_path: Path,
    capsys,
) -> None:
    database_path = tmp_path / "demo.sqlite3"
    database_path.write_text("legacy database", encoding="utf-8")
    env_file = _env_file(tmp_path, database_path)

    exit_code = wipe_database_script.main(
        [
            "--env-file",
            str(env_file),
            "--execute",
            "--confirm-wipe",
            "--database-name",
            database_path.name,
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert not database_path.exists()
    assert "Database wiped" in captured.out
    assert "wiped=True" in captured.out
    assert "uv run alembic upgrade head" in captured.out


def test_wipe_database_refuses_in_memory_sqlite() -> None:
    settings = Settings(
        _env_file=None,
        MY_AGENTS_RESPONSE_MODE="deterministic",
        MY_AGENTS_DATABASE_URL="sqlite+pysqlite:///:memory:",
    )

    try:
        wipe_database_script.wipe_database(settings=settings, dry_run=True)
    except ValueError as exc:
        assert "in-memory SQLite" in str(exc)
    else:  # pragma: no cover - failure path assertion helper
        raise AssertionError("in-memory SQLite wipe should be refused")
