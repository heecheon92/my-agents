"""Set a registered user's platform user_type through operator tooling only."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select

from my_agents.auth.contracts import UserType
from my_agents.auth.models import UserModel
from my_agents.persistence.database import (
    _sessionmaker_for_url,
    initialize_database,
    reset_database_caches,
)
from my_agents.settings import Settings
from scripts.ops_common import add_env_arguments, resolve_env_file


@dataclass(frozen=True)
class UserTypeUpdateResult:
    """Safe printable result for user-type updates."""

    email: str
    user_id: str
    account_type: str
    before_user_type: str
    after_user_type: str
    dry_run: bool


class UserTypeUpdateError(RuntimeError):
    """Raised when a requested user-type update is not allowed."""


def set_user_type(
    *,
    settings: Settings,
    user_type: UserType | str,
    email: str | None = None,
    user_id: str | None = None,
    dry_run: bool = False,
) -> UserTypeUpdateResult:
    """Set a non-guest user's platform user type, or preview the change."""
    if (email is None) == (user_id is None):
        raise UserTypeUpdateError("submit exactly one of email or user_id")
    resolved_user_type = UserType(user_type)

    reset_database_caches()
    initialize_database(settings)
    session_factory = _sessionmaker_for_url(settings.database_url)
    with session_factory() as db:
        user = _lookup_user(db, email=email, user_id=user_id)
        if user is None:
            raise UserTypeUpdateError("user not found")
        if user.account_type == "guest" and resolved_user_type in {UserType.ROOT, UserType.SYSTEM}:
            raise UserTypeUpdateError("guest accounts cannot be promoted to root/system")

        before = user.user_type or UserType.NORMAL.value
        result = UserTypeUpdateResult(
            email=user.email,
            user_id=user.id,
            account_type=user.account_type,
            before_user_type=before,
            after_user_type=resolved_user_type.value,
            dry_run=dry_run,
        )
        if dry_run:
            return result
        user.user_type = resolved_user_type.value
        db.add(user)
        db.commit()
        return result


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive user-type mutation parser."""
    parser = argparse.ArgumentParser(
        description="Set a registered user's platform user_type through operator tooling."
    )
    add_env_arguments(parser)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--email", help="Registered account email address.")
    identity.add_argument("--user-id", help="User id.")
    parser.add_argument(
        "--user-type",
        required=True,
        choices=tuple(item.value for item in UserType),
        help="Target platform user type.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the safe before/after metadata without persisting.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the user-type operator command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    env_file = resolve_env_file(profile=args.env, env_file=args.env_file)
    if not env_file.is_file():
        print(f"error: env file does not exist: {env_file}", file=sys.stderr)
        return 1
    settings = Settings(_env_file=env_file)
    try:
        result = set_user_type(
            settings=settings,
            email=args.email,
            user_id=args.user_id,
            user_type=UserType(args.user_type),
            dry_run=args.dry_run,
        )
    except UserTypeUpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("User type update preview" if result.dry_run else "User type updated")
    print(f"env_file={env_file}")
    print(f"email={result.email}")
    print(f"user_id={result.user_id}")
    print(f"account_type={result.account_type}")
    print(f"before_user_type={result.before_user_type}")
    print(f"after_user_type={result.after_user_type}")
    print(f"dry_run={result.dry_run}")
    return 0


def _lookup_user(db, *, email: str | None, user_id: str | None) -> UserModel | None:  # noqa: ANN001
    if email is not None:
        return db.scalar(select(UserModel).where(UserModel.email == email.strip().casefold()))
    return db.get(UserModel, user_id)


if __name__ == "__main__":
    raise SystemExit(main())
