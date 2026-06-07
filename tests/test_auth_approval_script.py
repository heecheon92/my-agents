"""Unified auth approval script tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scripts import auth_approval
from scripts.auth_approval import AccountApprovalCliResult, GuestCodeIssueCliResult


def _env_file(tmp_path: Path) -> Path:
    env_file = tmp_path / "operator.env"
    env_file.write_text("MY_AGENTS_RESPONSE_MODE=deterministic\n")
    return env_file


def test_interactive_account_approve_prompts_and_sends_email(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["", "User@Example.com", "y", "en"])
    calls: dict[str, dict[str, object]] = {}
    approved = AccountApprovalCliResult(
        email="user@example.com",
        user_id="user-id",
        verification_token="verify-token",
        verification_url="https://app.example.com/verify-email?token=verify-token",
    )

    def approve_account(**kwargs: object) -> AccountApprovalCliResult:
        calls["approve"] = kwargs
        return approved

    def send_account_verification_email(**kwargs: object) -> None:
        calls["send"] = kwargs

    monkeypatch.setattr(auth_approval, "approve_account", approve_account)
    monkeypatch.setattr(
        auth_approval,
        "send_account_verification_email",
        send_account_verification_email,
    )

    exit_code = auth_approval.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls["approve"]["email"] == "User@Example.com"
    assert calls["send"]["language"] == "en"
    assert "Auth approval interactive mode" in captured.out
    assert "Account signup approved" in captured.out
    assert "verification_token=verify-token" in captured.out
    assert "email_sent=True" in captured.out


def test_interactive_guest_issue_prompts_for_optional_values(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    env_file = _env_file(tmp_path)
    prompts = iter(["guest issue", "Guest@Example.com", "", "1800", "n"])
    calls: dict[str, dict[str, object]] = {}
    issued = GuestCodeIssueCliResult(
        email="guest@example.com",
        request_id="request-id",
        code="guest-code",
        expires_at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
    )

    def issue_guest_access_code(**kwargs: object) -> GuestCodeIssueCliResult:
        calls["issue"] = kwargs
        return issued

    monkeypatch.setattr(auth_approval, "issue_guest_access_code", issue_guest_access_code)

    exit_code = auth_approval.main(
        ["--env-file", str(env_file), "--interactive"],
        input_fn=lambda prompt: next(prompts),
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls["issue"]["email"] == "Guest@Example.com"
    assert calls["issue"]["ttl_seconds"] == 1800
    assert calls["issue"]["request_id"] is None
    assert "Guest access code issued" in captured.out
    assert "code=guest-code" in captured.out
    assert "email_sent=False" in captured.out
