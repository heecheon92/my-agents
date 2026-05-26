"""Issue a one-time guest access code for manual email delivery.

The public API records the requester email but never returns a guest code. Until SMTP/Resend is
wired, run this script against the same database, then manually send the printed code to the
requester.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta

from my_agents.auth.service import AuthService
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings


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
        description="Issue a one-time guest access code for manual email delivery."
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
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    # settings = Settings(_env_file=".env", response_mode="deterministic")
    settings = Settings(_env_file=".env.pgvector.local", response_mode="deterministic")
    result = issue_guest_access_code(
        settings=settings,
        email=args.email,
        ttl_seconds=args.ttl_seconds,
        request_id=args.request_id,
    )
    print("Guest access code issued")
    print(f"email={result.email}")
    print(f"request_id={result.request_id}")
    print(f"code={result.code}")
    print(f"expires_at={result.expires_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
