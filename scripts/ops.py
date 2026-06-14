"""Interactive operational CLI that delegates to focused script modules."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from scripts import (
    approve_account_signup,
    issue_guest_access_code,
    migrate_database,
    reject_account_signup,
    resend_account_verification,
    wipe_database,
)
from scripts.ops_common import add_env_arguments, env_argv


def build_parser() -> argparse.ArgumentParser:
    """Build a thin dispatcher parser for operator workflows."""
    parser = argparse.ArgumentParser(
        description="Interactive operator CLI for my-agents maintenance tasks."
    )
    add_env_arguments(parser)
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Prompt for operation-specific values interactively.",
    )
    subparsers = parser.add_subparsers(dest="resource")

    account = subparsers.add_parser("account", help="Account signup operations.")
    account_actions = account.add_subparsers(dest="action", required=True)
    account_approve = account_actions.add_parser("approve", help="Approve a pending account.")
    account_approve.add_argument("--email", required=True, help="Signup email address.")
    account_approve.add_argument(
        "--send-email",
        action="store_true",
        help="Also send the verification email using the selected env's provider.",
    )
    account_approve.add_argument(
        "--mark-verified",
        action="store_true",
        help="Set email_verified_at immediately instead of issuing a verification token.",
    )
    account_approve.add_argument(
        "--lang",
        choices=("ko", "en"),
        default="ko",
        help="Language for --send-email content. Defaults to ko; use en for English.",
    )
    account_reject = account_actions.add_parser("reject", help="Reject a pending account.")
    account_reject.add_argument("--email", required=True, help="Signup email address.")
    account_resend = account_actions.add_parser(
        "resend-verification",
        help="Create a fresh verification token for an approved unverified account.",
    )
    account_resend.add_argument("--email", required=True, help="Signup email address.")
    account_resend.add_argument(
        "--send-email",
        action="store_true",
        help="Also send the verification email using the selected env's provider.",
    )
    account_resend.add_argument(
        "--lang",
        choices=("ko", "en"),
        default="ko",
        help="Language for --send-email content. Defaults to ko; use en for English.",
    )
    guest = subparsers.add_parser("guest", help="Guest access operations.")
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
    database = subparsers.add_parser("database", help="Database maintenance operations.")
    database_actions = database.add_subparsers(dest="action", required=True)
    database_wipe = database_actions.add_parser(
        "wipe",
        help="Dangerously wipe the selected database after explicit confirmations.",
    )
    database_wipe.add_argument(
        "--execute",
        action="store_true",
        help="Actually wipe the selected database. Without this flag, only print the plan.",
    )
    database_wipe.add_argument(
        "--confirm-wipe",
        action="store_true",
        help="Required with --execute to acknowledge destructive data loss.",
    )
    database_wipe.add_argument(
        "--database-name",
        default=None,
        help="Required with --execute. Must exactly match the selected database name.",
    )
    database_wipe.add_argument(
        "--allow-remote-postgres",
        action="store_true",
        help="Allow a non-local Postgres host to be wiped. Still requires confirmations.",
    )
    database_migrate = database_actions.add_parser(
        "migrate",
        help="Check or run Alembic upgrade head for the selected database.",
    )
    database_migrate.add_argument(
        "--upgrade",
        action="store_true",
        help="Actually run Alembic upgrade head. Without this flag, only print status.",
    )
    database_migrate.add_argument(
        "--confirm-upgrade",
        action="store_true",
        help="Required with --upgrade to acknowledge production migration risk.",
    )
    database_migrate.add_argument(
        "--database-name",
        default=None,
        help="Required with --upgrade. Must exactly match the selected database name.",
    )
    database_migrate.add_argument(
        "--allow-remote-postgres",
        action="store_true",
        help="Allow a non-local Postgres host to be upgraded. Still requires confirmations.",
    )
    return parser


def main(argv: Sequence[str] | None = None, input_fn: Callable[[str], str] = input) -> int:
    """Prompt for or parse an operation, then delegate to the focused script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interactive:
        try:
            args = _interactive_args(args=args, input_fn=input_fn)
        except _InteractiveQuit:
            print("cancelled")
            return 0
    elif args.resource is None:
        parser.error(
            "the following arguments are required: {account,guest,database} or --interactive"
        )
    return _dispatch(args)


def _dispatch(args: argparse.Namespace) -> int:
    base_argv = env_argv(profile=args.env, env_file=args.env_file)
    if args.resource == "account" and args.action == "approve":
        delegated = [*base_argv, "--email", args.email]
        if args.mark_verified:
            delegated.append("--mark-verified")
        if args.send_email:
            delegated.append("--send-email")
        delegated.extend(["--lang", args.lang])
        return approve_account_signup.main(delegated)
    if args.resource == "account" and args.action == "reject":
        return reject_account_signup.main([*base_argv, "--email", args.email])
    if args.resource == "account" and args.action == "resend-verification":
        delegated = [*base_argv, "--email", args.email]
        if args.send_email:
            delegated.append("--send-email")
        delegated.extend(["--lang", args.lang])
        return resend_account_verification.main(delegated)
    if args.resource == "guest" and args.action == "issue":
        delegated = [*base_argv, "--email", args.email]
        if args.request_id:
            delegated.extend(["--request-id", args.request_id])
        if args.ttl_seconds is not None:
            delegated.extend(["--ttl-seconds", str(args.ttl_seconds)])
        if args.send_email:
            delegated.append("--send-email")
        delegated.extend(["--lang", args.lang])
        return issue_guest_access_code.main(delegated)
    if args.resource == "database" and args.action == "wipe":
        delegated = [*base_argv]
        if args.execute:
            delegated.append("--execute")
        if args.confirm_wipe:
            delegated.append("--confirm-wipe")
        if args.database_name:
            delegated.extend(["--database-name", args.database_name])
        if args.allow_remote_postgres:
            delegated.append("--allow-remote-postgres")
        return wipe_database.main(delegated)
    if args.resource == "database" and args.action == "migrate":
        delegated = [*base_argv]
        if args.upgrade:
            delegated.append("--upgrade")
        if args.confirm_upgrade:
            delegated.append("--confirm-upgrade")
        if args.database_name:
            delegated.extend(["--database-name", args.database_name])
        if args.allow_remote_postgres:
            delegated.append("--allow-remote-postgres")
        return migrate_database.main(delegated)
    raise ValueError(f"unsupported operation: {args.resource} {args.action}")


def _interactive_args(
    *,
    args: argparse.Namespace,
    input_fn: Callable[[str], str],
) -> argparse.Namespace:
    print("my-agents operational CLI")
    operation = _prompt_choice(
        input_fn,
        "Operation",
        choices=(
            ("account approve", "Approve pending account signup"),
            ("account reject", "Reject pending account signup"),
            (
                "account resend-verification",
                "Resend verification for approved unverified account",
            ),
            ("guest issue", "Issue one-time guest access code"),
            ("database wipe", "DANGER: wipe the selected database"),
            ("database migrate", "Check or run Alembic upgrade head"),
        ),
        default="account approve",
    )
    args.resource, args.action = operation.split(" ", 1)
    args.email = None
    args.lang = "ko"
    args.send_email = False
    args.request_id = None
    args.ttl_seconds = None
    if args.resource == "account" and args.action == "approve":
        args.email = _prompt_required(input_fn, "Email")
        args.mark_verified = _prompt_bool(
            input_fn,
            "Mark email verified now instead of issuing a verification link",
            default=False,
        )
        if not args.mark_verified:
            args.send_email = _prompt_bool(input_fn, "Send verification email", default=False)
            if args.send_email:
                args.lang = _prompt_choice(
                    input_fn,
                    "Email language",
                    choices=(("ko", "Korean"), ("en", "English")),
                    default="ko",
                )
    elif args.resource == "account" and args.action == "resend-verification":
        args.email = _prompt_required(input_fn, "Email")
        args.mark_verified = False
        args.send_email = _prompt_bool(input_fn, "Send verification email", default=False)
        if args.send_email:
            args.lang = _prompt_choice(
                input_fn,
                "Email language",
                choices=(("ko", "Korean"), ("en", "English")),
                default="ko",
            )
    elif args.resource == "account" and args.action == "reject":
        args.email = _prompt_required(input_fn, "Email")
        confirmed = _prompt_bool(input_fn, "Reject this pending account", default=False)
        if not confirmed:
            raise SystemExit("cancelled")
    elif args.resource == "guest" and args.action == "issue":
        args.email = _prompt_required(input_fn, "Email")
        args.request_id = _prompt_optional(input_fn, "Guest request id")
        ttl_seconds = _prompt_optional(input_fn, "Guest code TTL seconds")
        args.ttl_seconds = int(ttl_seconds) if ttl_seconds else None
        args.send_email = _prompt_bool(input_fn, "Send guest code email", default=False)
        if args.send_email:
            args.lang = _prompt_choice(
                input_fn,
                "Email language",
                choices=(("ko", "Korean"), ("en", "English")),
                default="ko",
            )
    elif args.resource == "database" and args.action == "wipe":
        print(f"\n{wipe_database.WIPE_WARNING}")
        args.execute = _prompt_bool(
            input_fn,
            "Execute the wipe now instead of showing a dry-run plan",
            default=False,
        )
        args.confirm_wipe = False
        args.database_name = None
        args.allow_remote_postgres = False
        if args.execute:
            args.confirm_wipe = _prompt_bool(
                input_fn,
                "I understand this permanently deletes the selected database",
                default=False,
            )
            args.database_name = _prompt_required(input_fn, "Exact database name")
            args.allow_remote_postgres = _prompt_bool(
                input_fn,
                "Allow wiping a non-local Postgres host if the env points to one",
                default=False,
            )
    elif args.resource == "database" and args.action == "migrate":
        print(f"\n{migrate_database.MIGRATION_WARNING}")
        args.upgrade = _prompt_bool(
            input_fn,
            "Run Alembic upgrade head now instead of showing migration status",
            default=False,
        )
        args.confirm_upgrade = False
        args.database_name = None
        args.allow_remote_postgres = False
        if args.upgrade:
            args.confirm_upgrade = _prompt_bool(
                input_fn,
                "I have taken a provider snapshot/backup and verified the env target",
                default=False,
            )
            args.database_name = _prompt_required(input_fn, "Exact database name")
            args.allow_remote_postgres = _prompt_bool(
                input_fn,
                "Allow upgrading a non-local Postgres host if the env points to one",
                default=False,
            )
    return args


class _InteractiveQuit(Exception):
    """Raised when an operator exits an interactive prompt."""


def _prompt_choice(
    input_fn: Callable[[str], str],
    label: str,
    *,
    choices: tuple[tuple[str, str], ...],
    default: str,
) -> str:
    choice_values = {value for value, _description in choices}
    default_index = _choice_index(choices=choices, value=default)
    print(f"\n{label}:")
    for index, (value, description) in enumerate(choices, start=1):
        default_marker = " (default)" if value == default else ""
        print(f"  {index}. {description} [{value}]{default_marker}")
    while True:
        value = input_fn(f"Choose {label.lower()} [{default_index}]: ").strip().casefold()
        if not value:
            return default
        _raise_if_quit(value)
        if value.isdigit():
            index = int(value)
            if 1 <= index <= len(choices):
                return choices[index - 1][0]
        if value in choice_values:
            return value
        print(f"Please choose a number from 1 to {len(choices)}.")


def _choice_index(*, choices: tuple[tuple[str, str], ...], value: str) -> int:
    for index, (choice_value, _description) in enumerate(choices, start=1):
        if choice_value == value:
            return index
    raise ValueError(f"default choice is not in choices: {value}")


def _prompt_required(input_fn: Callable[[str], str], label: str) -> str:
    while True:
        value = input_fn(f"{label} (q to quit): ").strip()
        _raise_if_quit(value)
        if value:
            return value
        print(f"{label} is required.")


def _prompt_optional(input_fn: Callable[[str], str], label: str) -> str | None:
    value = input_fn(f"{label} (optional, q to quit): ").strip()
    _raise_if_quit(value)
    return value or None


def _raise_if_quit(value: str) -> None:
    if value.casefold() in {"q", "quit", "exit"}:
        raise _InteractiveQuit


def _prompt_bool(input_fn: Callable[[str], str], label: str, *, default: bool) -> bool:
    default_index = 1 if default else 2
    print(f"\n{label}?")
    print(f"  1. Yes{' (default)' if default else ''}")
    print(f"  2. No{' (default)' if not default else ''}")
    while True:
        value = input_fn(f"Choose yes/no [{default_index}]: ").strip().casefold()
        if not value:
            return default
        _raise_if_quit(value)
        if value in {"1", "y", "yes"}:
            return True
        if value in {"2", "n", "no"}:
            return False
        print("Please choose 1 for yes or 2 for no.")


if __name__ == "__main__":
    raise SystemExit(main())
