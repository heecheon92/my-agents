"""Reject a pending account signup."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from my_agents.auth.service import AuthService
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings
from scripts.ops_common import add_env_arguments, resolve_env_file


def reject_account_signup(*, settings: Settings, email: str) -> str:
    """Reject a pending registered account and return its normalized email."""
    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        user = AuthService(db).reject_account_signup(email=email)
        return user.email


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive account rejection parser."""
    parser = argparse.ArgumentParser(description="Reject a pending account signup.")
    add_env_arguments(parser)
    parser.add_argument("--email", required=True, help="Signup email address.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the account rejection command."""
    args = build_parser().parse_args(argv)
    env_file = resolve_env_file(profile=args.env, env_file=args.env_file)
    if not env_file.is_file():
        print(f"error: env file does not exist: {env_file}", file=sys.stderr)
        return 1
    settings = Settings(_env_file=env_file)
    email = reject_account_signup(settings=settings, email=args.email)
    print("Account signup rejected")
    print(f"env_file={env_file}")
    print(f"email={email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
