"""Issue a one-time guest access code for operator delivery.

The public API records the requester email but never returns a guest code. Run this script
against the same database to print the code, and optionally add `--send-email` to deliver the
same code through the selected environment's configured auth email provider.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

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
class GuestCodeIssueResult:
    """Printable guest-code issue result."""

    email: str
    request_id: str | None
    code: str
    expires_at: datetime


def issue_guest_access_code(
    *,
    settings: Settings,
    email: str,
    ttl_seconds: int | None = None,
    request_id: str | None = None,
) -> GuestCodeIssueResult:
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
        return GuestCodeIssueResult(
            email=email.strip().casefold(),
            request_id=result.request_id,
            code=result.code,
            expires_at=result.expires_at,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue a one-time guest access code for operator delivery."
    )
    parser.add_argument("--email", required=True, help="Requester email address.")
    parser.add_argument(
        "--request-id",
        default=None,
        help="Optional guest_access_requests.id to issue against.",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=None,
        help="Override guest code TTL seconds. Defaults to MY_AGENTS_GUEST_CODE_TTL_SECONDS.",
    )
    parser.add_argument(
        "--env",
        choices=tuple(ENV_FILE_BY_PROFILE),
        default="pgvector.local",
        help=(
            "Named env file to load. Defaults to pgvector.local for safety; "
            "use pgvector.production only when intentionally issuing against production."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Explicit env file path. Overrides --env when provided.",
    )
    parser.add_argument(
        "--send-email",
        action="store_true",
        help="Also send the issued code to --email using the selected env's email provider.",
    )
    parser.add_argument(
        "--lang",
        choices=("ko", "en"),
        default="ko",
        help="Language for --send-email content. Defaults to ko; use en for English.",
    )
    return parser


def resolve_env_file(*, profile: str, env_file: Path | None = None) -> Path:
    """Resolve the env file used for the guest-code issue operation."""
    selected = env_file or ENV_FILE_BY_PROFILE[profile]
    return selected.expanduser()


def send_guest_access_code_email(
    *,
    settings: Settings,
    result: GuestCodeIssueResult,
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


def main() -> int:
    args = _build_parser().parse_args()
    env_file = resolve_env_file(profile=args.env, env_file=args.env_file)
    if not env_file.is_file():
        print(f"error: env file does not exist: {env_file}", file=sys.stderr)
        return 1
    settings = Settings(_env_file=env_file)
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


if __name__ == "__main__":
    raise SystemExit(main())
