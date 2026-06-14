"""Interactive operational CLI dispatcher tests."""

from __future__ import annotations

from pathlib import Path

from scripts import auth_approval, ops


def _env_file(tmp_path: Path) -> Path:
    env_file = tmp_path / "operator.env"
    env_file.write_text("MY_AGENTS_RESPONSE_MODE=deterministic\n")
    return env_file


def test_interactive_account_approve_delegates_to_functional_script(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["", "User@Example.com", "n", "y", "en"])
    delegated: dict[str, list[str]] = {}

    def approve_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.approve_account_signup, "main", approve_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--email",
        "User@Example.com",
        "--send-email",
        "--lang",
        "en",
    ]
    assert "my-agents operational CLI" in captured.out


def test_interactive_guest_issue_delegates_to_functional_script(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["guest issue", "Guest@Example.com", "", "1800", "n"])
    delegated: dict[str, list[str]] = {}

    def issue_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.issue_guest_access_code, "main", issue_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--email",
        "Guest@Example.com",
        "--ttl-seconds",
        "1800",
        "--lang",
        "ko",
    ]


def test_interactive_account_approve_accepts_numbered_menu_choices(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["1", "User@Example.com", "2", "1", "2"])
    delegated: dict[str, list[str]] = {}

    def approve_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.approve_account_signup, "main", approve_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--email",
        "User@Example.com",
        "--send-email",
        "--lang",
        "en",
    ]
    assert "1. Approve pending account signup [account approve]" in captured.out
    assert "2. English [en]" in captured.out


def test_interactive_guest_issue_accepts_numbered_operation_choice(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["4", "Guest@Example.com", "request-123", "", "2"])
    delegated: dict[str, list[str]] = {}

    def issue_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.issue_guest_access_code, "main", issue_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--email",
        "Guest@Example.com",
        "--request-id",
        "request-123",
        "--lang",
        "ko",
    ]


def test_interactive_database_wipe_prints_strong_warning_and_delegates(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["database wipe", "yes", "yes", "my_agents", "no"])
    delegated: dict[str, list[str]] = {}

    def wipe_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.wipe_database, "main", wipe_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "!!! DANGER: DATABASE WIPE" in captured.out
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--execute",
        "--confirm-wipe",
        "--database-name",
        "my_agents",
    ]


def test_database_wipe_command_delegates_to_functional_script(monkeypatch) -> None:  # noqa: ANN001
    delegated: dict[str, list[str]] = {}

    def wipe_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.wipe_database, "main", wipe_main)

    exit_code = ops.main(
        [
            "--env",
            "pgvector.production",
            "database",
            "wipe",
            "--execute",
            "--confirm-wipe",
            "--database-name",
            "my_agents_prod",
            "--allow-remote-postgres",
        ]
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env",
        "pgvector.production",
        "--execute",
        "--confirm-wipe",
        "--database-name",
        "my_agents_prod",
        "--allow-remote-postgres",
    ]


def test_interactive_database_migrate_status_delegates_to_functional_script(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["database migrate", "no"])
    delegated: dict[str, list[str]] = {}

    def migrate_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.migrate_database, "main", migrate_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "DATABASE MIGRATION" in captured.out
    assert delegated["argv"] == ["--env-file", str(env_file)]


def test_interactive_database_migrate_upgrade_delegates_with_confirmations(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["6", "yes", "yes", "my_agents_prod", "yes"])
    delegated: dict[str, list[str]] = {}

    def migrate_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.migrate_database, "main", migrate_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--upgrade",
        "--confirm-upgrade",
        "--database-name",
        "my_agents_prod",
        "--allow-remote-postgres",
    ]


def test_database_migrate_command_delegates_to_functional_script(monkeypatch) -> None:  # noqa: ANN001
    delegated: dict[str, list[str]] = {}

    def migrate_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.migrate_database, "main", migrate_main)

    exit_code = ops.main(
        [
            "--env",
            "pgvector.production",
            "database",
            "migrate",
            "--upgrade",
            "--confirm-upgrade",
            "--database-name",
            "my_agents_prod",
            "--allow-remote-postgres",
        ]
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env",
        "pgvector.production",
        "--upgrade",
        "--confirm-upgrade",
        "--database-name",
        "my_agents_prod",
        "--allow-remote-postgres",
    ]


def test_interactive_account_approve_can_mark_email_verified(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["1", "User@Example.com", "1"])
    delegated: dict[str, list[str]] = {}

    def approve_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.approve_account_signup, "main", approve_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--email",
        "User@Example.com",
        "--mark-verified",
        "--lang",
        "ko",
    ]


def test_account_approve_mark_verified_command_delegates_to_functional_script(monkeypatch) -> None:  # noqa: ANN001
    delegated: dict[str, list[str]] = {}

    def approve_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.approve_account_signup, "main", approve_main)

    exit_code = ops.main(
        [
            "--env",
            "pgvector.production",
            "account",
            "approve",
            "--email",
            "user@example.com",
            "--mark-verified",
        ]
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env",
        "pgvector.production",
        "--email",
        "user@example.com",
        "--mark-verified",
        "--lang",
        "ko",
    ]


def test_interactive_account_resend_verification_delegates_to_functional_script(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["3", "User@Example.com", "1", "2"])
    delegated: dict[str, list[str]] = {}

    def resend_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.resend_account_verification, "main", resend_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env-file",
        str(env_file),
        "--email",
        "User@Example.com",
        "--send-email",
        "--lang",
        "en",
    ]
    assert "3. Resend verification for approved unverified account" in captured.out


def test_account_resend_verification_command_delegates_to_functional_script(monkeypatch) -> None:  # noqa: ANN001
    delegated: dict[str, list[str]] = {}

    def resend_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.resend_account_verification, "main", resend_main)

    exit_code = ops.main(
        [
            "--env",
            "pgvector.production",
            "account",
            "resend-verification",
            "--email",
            "user@example.com",
            "--send-email",
            "--lang",
            "en",
        ]
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env",
        "pgvector.production",
        "--email",
        "user@example.com",
        "--send-email",
        "--lang",
        "en",
    ]


def test_interactive_quit_from_operation_menu_exits_cleanly(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)

    def approve_main(argv: list[str]) -> int:
        raise AssertionError("functional script should not run after quit")

    monkeypatch.setattr(ops.approve_account_signup, "main", approve_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: "q",
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "cancelled" in captured.out


def test_interactive_quit_from_email_prompt_exits_cleanly(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["1", "q"])

    def approve_main(argv: list[str]) -> int:
        raise AssertionError("functional script should not run after quit")

    monkeypatch.setattr(ops.approve_account_signup, "main", approve_main)

    exit_code = ops.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "cancelled" in captured.out


def test_legacy_auth_approval_entrypoint_delegates_to_ops(monkeypatch) -> None:  # noqa: ANN001
    delegated: dict[str, object] = {}

    def approve_main(argv: list[str]) -> int:
        delegated["argv"] = argv
        return 0

    monkeypatch.setattr(ops.approve_account_signup, "main", approve_main)

    exit_code = auth_approval.main(
        ["--env", "pgvector.production", "account", "approve", "--email", "user@example.com"]
    )

    assert exit_code == 0
    assert delegated["argv"] == [
        "--env",
        "pgvector.production",
        "--email",
        "user@example.com",
        "--lang",
        "ko",
    ]
