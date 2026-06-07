"""Operator approval CLI for pending account signups and guest codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from my_agents.auth.email import AuthEmailLanguage, build_auth_email_sender
from my_agents.auth.service import AuthService
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings

ENV_FILE_BY_PROFILE = {
    "pgvector.local": Path(".env.pgvector.local"),
    "pgvector.production": Path(".env.pgvector.production"),
}


@dataclass(frozen=True)
class AccountApprovalCliResult:
    """Printable account approval result."""

    email: str
    user_id: str
    verification_token: str
    verification_url: str | None


@dataclass(frozen=True)
class GuestCodeIssueCliResult:
    """Printable guest-code issue result."""

    email: str
    request_id: str | None
    code: str
    expires_at: datetime


def approve_account(
    *,
    settings: Settings,
    email: str,
) -> AccountApprovalCliResult:
    """Approve a pending registered account and return a printable verification token."""
    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        result = AuthService(db).approve_account_signup(email=email)
        return AccountApprovalCliResult(
            email=result.user.email,
            user_id=result.user.id,
            verification_token=result.verification_token,
            verification_url=_action_url(settings, "/verify-email", result.verification_token),
        )


def reject_account(*, settings: Settings, email: str) -> str:
    """Reject a pending registered account and return its normalized email."""
    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        user = AuthService(db).reject_account_signup(email=email)
        return user.email


def issue_guest_access_code(
    *,
    settings: Settings,
    email: str,
    ttl_seconds: int | None = None,
    request_id: str | None = None,
) -> GuestCodeIssueCliResult:
    """Create a one-time guest code linked to a pending email request when present."""
    if not settings.guest_access_enabled:
        raise ValueError("guest access is disabled; set MY_AGENTS_GUEST_ACCESS_ENABLED=true")
    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        result = AuthService(db).issue_guest_access_code(
            email=email,
            ttl=timedelta(seconds=ttl_seconds or settings.guest_code_ttl_seconds),
            request_id=request_id,
        )
        return GuestCodeIssueCliResult(
            email=email.strip().casefold(),
            request_id=result.request_id,
            code=result.code,
            expires_at=result.expires_at,
        )


def send_account_verification_email(
    *,
    settings: Settings,
    result: AccountApprovalCliResult,
    language: AuthEmailLanguage = "ko",
) -> None:
    """Send a verification email for an approved account."""
    sender = build_auth_email_sender(settings)
    sender.send_email_verification(
        recipient_email=result.email,
        token=result.verification_token,
        language=language,
    )


def send_guest_access_code_email(
    *,
    settings: Settings,
    result: GuestCodeIssueCliResult,
    language: AuthEmailLanguage = "ko",
) -> None:
    """Send an issued guest access code through the configured auth email sender."""
    sender = build_auth_email_sender(settings)
    sender.send_guest_access_code(
        recipient_email=result.email,
        code=result.code,
        expires_at=result.expires_at,
        language=language,
    )


def resolve_env_file(*, profile: str, env_file: Path | None = None) -> Path:
    """Resolve the env file used for approval operations."""
    selected = env_file or ENV_FILE_BY_PROFILE[profile]
    return selected.expanduser()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Approve pending auth requests or issue guest access codes."
    )
    parser.add_argument(
        "--env",
        choices=tuple(ENV_FILE_BY_PROFILE),
        default="pgvector.local",
        help=(
            "Named env file to load. Defaults to pgvector.local for safety; "
            "use pgvector.production only when intentionally operating on production."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Explicit env file path. Overrides --env when provided.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Prompt for the operation and required values interactively.",
    )
    subparsers = parser.add_subparsers(dest="resource")

    account = subparsers.add_parser("account", help="Approve or reject account signups.")
    account_actions = account.add_subparsers(dest="action", required=True)
    account_approve = account_actions.add_parser("approve", help="Approve a pending account.")
    account_approve.add_argument("--email", required=True, help="Signup email address.")
    account_approve.add_argument(
        "--send-email",
        action="store_true",
        help="Also send the verification email using the selected env's provider.",
    )
    account_approve.add_argument(
        "--lang",
        choices=("ko", "en"),
        default="ko",
        help="Language for --send-email content. Defaults to ko; use en for English.",
    )
    account_reject = account_actions.add_parser("reject", help="Reject a pending account.")
    account_reject.add_argument("--email", required=True, help="Signup email address.")

    guest = subparsers.add_parser("guest", help="Issue guest access codes.")
    guest_actions = guest.add_subparsers(dest="action", required=True)
    guest_issue = guest_actions.add_parser("issue", help="Issue a one-time guest code.")
    guest_issue.add_argument("--email", required=True, help="Requester email address.")
    guest_issue.add_argument(
        "--request-id",
        default=None,
        help="Optional guest_access_requests.id to issue against.",
    )
    guest_issue.add_argument(
        "--ttl-seconds",
        type=int,
        default=None,
        help="Override guest code TTL seconds. Defaults to MY_AGENTS_GUEST_CODE_TTL_SECONDS.",
    )
    guest_issue.add_argument(
        "--send-email",
        action="store_true",
        help="Also send the issued code to --email using the selected env's email provider.",
    )
    guest_issue.add_argument(
        "--lang",
        choices=("ko", "en"),
        default="ko",
        help="Language for --send-email content. Defaults to ko; use en for English.",
    )
    return parser


def main(argv: Sequence[str] | None = None, input_fn: Callable[[str], str] = input) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.interactive:
        args = _interactive_args(args=args, input_fn=input_fn)
    elif args.resource is None:
        parser.error("the following arguments are required: {account,guest} or --interactive")
    env_file = resolve_env_file(profile=args.env, env_file=args.env_file)
    if not env_file.is_file():
        print(f"error: env file does not exist: {env_file}", file=sys.stderr)
        return 1
    settings = Settings(_env_file=env_file)
    if args.resource == "account" and args.action == "approve":
        return _approve_account_main(args=args, settings=settings, env_file=env_file)
    if args.resource == "account" and args.action == "reject":
        email = reject_account(settings=settings, email=args.email)
        print("Account signup rejected")
        print(f"env_file={env_file}")
        print(f"email={email}")
        return 0
    if args.resource == "guest" and args.action == "issue":
        return _issue_guest_main(args=args, settings=settings, env_file=env_file)
    print("error: unknown command", file=sys.stderr)
    return 2


def _interactive_args(
    *,
    args: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> argparse.Namespace:
    print("Auth approval interactive mode")
    operation = _prompt_choice(
        input_fn,
        "Operation",
        choices=("account approve", "account reject", "guest issue"),
        default="account approve",
    )
    args.resource, args.action = operation.split(" ", 1)
    args.email = _prompt_required(input_fn, "Email")
    args.lang = "ko"
    args.send_email = False
    args.request_id = None
    args.ttl_seconds = None
    if args.resource == "account" and args.action == "approve":
        args.send_email = _prompt_bool(input_fn, "Send verification email", default=False)
        if args.send_email:
            args.lang = _prompt_choice(
                input_fn,
                "Email language",
                choices=("ko", "en"),
                default="ko",
            )
    elif args.resource == "account" and args.action == "reject":
        confirmed = _prompt_bool(input_fn, "Reject this pending account", default=False)
        if not confirmed:
            raise SystemExit("cancelled")
    elif args.resource == "guest" and args.action == "issue":
        args.request_id = _prompt_optional(input_fn, "Guest request id")
        ttl_seconds = _prompt_optional(input_fn, "Guest code TTL seconds")
        args.ttl_seconds = int(ttl_seconds) if ttl_seconds else None
        args.send_email = _prompt_bool(input_fn, "Send guest code email", default=False)
        if args.send_email:
            args.lang = _prompt_choice(
                input_fn,
                "Email language",
                choices=("ko", "en"),
                default="ko",
            )
    return args


def _prompt_choice(
    input_fn: Callable[[str], str],
    label: str,
    *,
    choices: tuple[str, ...],
    default: str,
) -> str:
    choices_text = "/".join(choices)
    while True:
        value = input_fn(f"{label} [{choices_text}] ({default}): ").strip().casefold()
        if not value:
            return default
        if value in choices:
            return value
        print(f"Please enter one of: {choices_text}")


def _prompt_required(input_fn: Callable[[str], str], label: str) -> str:
    while True:
        value = input_fn(f"{label}: ").strip()
        if value:
            return value
        print(f"{label} is required.")


def _prompt_optional(input_fn: Callable[[str], str], label: str) -> str | None:
    value = input_fn(f"{label} (optional): ").strip()
    return value or None


def _prompt_bool(input_fn: Callable[[str], str], label: str, *, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        value = input_fn(f"{label}? [{default_text}]: ").strip().casefold()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please enter y or n.")


def _approve_account_main(
    *,
    args: argparse.Namespace,
    settings: Settings,
    env_file: Path,
) -> int:
    result = approve_account(settings=settings, email=args.email)
    print("Account signup approved")
    print(f"env_file={env_file}")
    print(f"email={result.email}")
    print(f"user_id={result.user_id}")
    print(f"verification_token={result.verification_token}")
    print(f"verification_url={result.verification_url or ''}")
    email_sent = False
    if args.send_email:
        try:
            send_account_verification_email(settings=settings, result=result, language=args.lang)
        except Exception as exc:
            print(f"email_sent={email_sent}")
            print(f"email_language={args.lang}")
            print(
                f"error: failed to send account verification email: {exc.__class__.__name__}",
                file=sys.stderr,
            )
            return 1
        email_sent = True
    print(f"email_sent={email_sent}")
    print(f"email_language={args.lang}")
    return 0


def _issue_guest_main(
    *,
    args: argparse.Namespace,
    settings: Settings,
    env_file: Path,
) -> int:
    result = issue_guest_access_code(
        settings=settings,
        email=args.email,
        ttl_seconds=args.ttl_seconds,
        request_id=args.request_id,
    )
    print("Guest access code issued")
    print(f"env_file={env_file}")
    print(f"email={result.email}")
    print(f"request_id={result.request_id}")
    print(f"code={result.code}")
    print(f"expires_at={result.expires_at.isoformat()}")
    email_sent = False
    if args.send_email:
        try:
            send_guest_access_code_email(settings=settings, result=result, language=args.lang)
        except Exception as exc:
            print(f"email_sent={email_sent}")
            print(f"email_language={args.lang}")
            print(
                f"error: failed to send guest access code email: {exc.__class__.__name__}",
                file=sys.stderr,
            )
            return 1
        email_sent = True
    print(f"email_sent={email_sent}")
    print(f"email_language={args.lang}")
    return 0


def _action_url(settings: Settings, path: str, token: str) -> str | None:
    if settings.auth_public_app_base_url is None:
        return None
    return f"{settings.auth_public_app_base_url.rstrip('/')}{path}?token={quote(token, safe='')}"


if __name__ == "__main__":
    raise SystemExit(main())
