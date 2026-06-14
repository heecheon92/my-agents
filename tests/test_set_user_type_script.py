"""Operator-only user_type script tests."""

from __future__ import annotations

from pathlib import Path

from my_agents.auth.models import UserModel
from my_agents.auth.service import AuthService
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings
from scripts.set_user_type import UserTypeUpdateError, main, set_user_type


def _settings(monkeypatch, database_url: str) -> Settings:  # noqa: ANN001
    monkeypatch.setenv("MY_AGENTS_RESPONSE_MODE", "deterministic")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", database_url)
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    return Settings(_env_file=None)


def _create_user(settings: Settings, email: str) -> str:
    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        result = AuthService(db).signup(
            email=email,
            nickname="Script User",
            password="correct horse battery staple",
            auto_approve=True,
        )
        return result.user.id


def _user_type(database_url: str, user_id: str) -> str:
    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        user = db.get(UserModel, user_id)
        assert user is not None
        return user.user_type


def test_set_user_type_dry_run_does_not_persist(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'user-type-dry-run.db'}"
    settings = _settings(monkeypatch, database_url)
    user_id = _create_user(settings, "Dry.Run@Example.com")

    result = set_user_type(
        settings=settings,
        email="dry.run@example.com",
        user_type="root",
        dry_run=True,
    )

    assert result.email == "dry.run@example.com"
    assert result.user_id == user_id
    assert result.before_user_type == "normal"
    assert result.after_user_type == "root"
    assert result.dry_run is True
    assert _user_type(database_url, user_id) == "normal"


def test_set_user_type_persists_by_user_id_and_can_demote(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'user-type-persist.db'}"
    settings = _settings(monkeypatch, database_url)
    user_id = _create_user(settings, "persist@example.com")

    promoted = set_user_type(
        settings=settings,
        user_id=user_id,
        user_type="system",
    )
    demoted = set_user_type(
        settings=settings,
        user_id=user_id,
        user_type="normal",
    )

    assert promoted.after_user_type == "system"
    assert demoted.before_user_type == "system"
    assert demoted.after_user_type == "normal"
    assert _user_type(database_url, user_id) == "normal"


def test_set_user_type_refuses_missing_or_ambiguous_identifiers(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'user-type-errors.db'}"
    settings = _settings(monkeypatch, database_url)
    user_id = _create_user(settings, "ambiguous@example.com")

    for kwargs in (
        {"email": None, "user_id": None},
        {"email": "ambiguous@example.com", "user_id": user_id},
    ):
        try:
            set_user_type(settings=settings, user_type="root", **kwargs)
        except UserTypeUpdateError as exc:
            assert "exactly one" in str(exc)
        else:  # pragma: no cover - defensive assertion branch
            raise AssertionError("expected UserTypeUpdateError")


def test_set_user_type_refuses_guest_promotion(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    database_url = f"sqlite+pysqlite:///{tmp_path / 'user-type-guest.db'}"
    settings = _settings(monkeypatch, database_url)
    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(database_url)
    with session_factory() as db:
        guest = UserModel(
            id="guest-user-id",
            email="guest@example.com",
            nickname="Guest",
            password_hash="guest-login-disabled",
            account_type="guest",
        )
        db.add(guest)
        db.commit()

    try:
        set_user_type(settings=settings, user_id="guest-user-id", user_type="root")
    except UserTypeUpdateError as exc:
        assert "guest accounts cannot be promoted" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("expected UserTypeUpdateError")


def test_main_prints_safe_metadata(tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
    database_path = tmp_path / "user-type-main.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    env_file = _env_file(tmp_path, database_url)
    settings = _settings(monkeypatch, database_url)
    _create_user(settings, "main@example.com")

    exit_code = main(
        [
            "--env-file",
            str(env_file),
            "--email",
            "main@example.com",
            "--user-type",
            "root",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "email=main@example.com" in captured.out
    assert "before_user_type=normal" in captured.out
    assert "after_user_type=root" in captured.out
    assert "dry_run=True" in captured.out
    assert "password" not in captured.out.lower()
    assert "token" not in captured.out.lower()


def test_main_invalid_identifier_returns_nonzero(tmp_path, capsys) -> None:
    env_file = _env_file(tmp_path, f"sqlite+pysqlite:///{tmp_path / 'missing.db'}")

    exit_code = main(
        [
            "--env-file",
            str(env_file),
            "--email",
            "missing@example.com",
            "--user-type",
            "system",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "user not found" in captured.err


def _env_file(tmp_path: Path, database_url: str) -> Path:
    env_file = tmp_path / "operator.env"
    env_file.write_text(
        "\n".join(
            [
                "MY_AGENTS_RESPONSE_MODE=deterministic",
                f"MY_AGENTS_DATABASE_URL={database_url}",
                "MY_AGENTS_AUTO_CREATE_TABLES=true",
            ]
        )
    )
    return env_file
