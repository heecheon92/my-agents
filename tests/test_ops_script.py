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
    prompts = iter(["", "User@Example.com", "y", "en"])
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
