"""Resend account email verification for an approved, unverified signup."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote

from my_agents.auth.email import AuthEmailLanguage, build_auth_email_sender
from my_agents.auth.service import AuthService
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings
from scripts.ops_common import add_env_arguments, resolve_env_file


@dataclass(frozen=True)
class AccountVerificationResendResult:
    """Printable account verification resend result."""

    email: str
    user_id: str
    verification_token: str
    verification_url: str | None


def resend_account_verification(
    *,
    settings: Settings,
    email: str,
) -> AccountVerificationResendResult:
    """Create a fresh email-verification token for an approved unverified account."""
    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        result = AuthService(db).resend_account_verification(email=email)
        return AccountVerificationResendResult(
            email=result.user.email,
            user_id=result.user.id,
            verification_token=result.verification_token,
            verification_url=_action_url(settings, "/verify-email", result.verification_token),
        )


def send_account_verification_email(
    *,
    settings: Settings,
    result: AccountVerificationResendResult,
    language: AuthEmailLanguage = "ko",
) -> None:
    """Send a fresh verification email for an approved account."""
    sender = build_auth_email_sender(settings)
    sender.send_email_verification(
        recipient_email=result.email,
        token=result.verification_token,
        language=language,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive account verification resend parser."""
    parser = argparse.ArgumentParser(
        description="Resend account email verification for an approved, unverified signup."
    )
    add_env_arguments(parser)
    parser.add_argument("--email", required=True, help="Signup email address.")
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Also send the verification email using the selected env's provider.",
    )
    parser.add_argument(
        "--lang",
        choices=("ko", "en"),
        default="ko",
        help="Language for --send-email content. Defaults to ko; use en for English.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the account verification resend command."""
    args = build_parser().parse_args(argv)
    env_file = resolve_env_file(profile=args.env, env_file=args.env_file)
    if not env_file.is_file():
        print(f"error: env file does not exist: {env_file}", file=sys.stderr)
        return 1
    settings = Settings(_env_file=env_file)
    result = resend_account_verification(settings=settings, email=args.email)
    print("Account verification token refreshed")
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


def _action_url(settings: Settings, path: str, token: str) -> str | None:
    if settings.auth_public_app_base_url is None:
        return None
    return f"{settings.auth_public_app_base_url.rstrip('/')}{path}?token={quote(token, safe='')}"


if __name__ == "__main__":
    raise SystemExit(main())
