"""Interactive operational CLI that delegates to focused script modules."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from scripts import approve_account_signup, issue_guest_access_code, reject_account_signup
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
        "--lang",
        choices=("ko", "en"),
        default="ko",
        help="Language for --send-email content. Defaults to ko; use en for English.",
    )
    account_reject = account_actions.add_parser("reject", help="Reject a pending account.")
    account_reject.add_argument("--email", required=True, help="Signup email address.")

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
    return parser


def main(argv: Sequence[str] | None = None, input_fn: Callable[[str], str] = input) -> int:
    """Prompt for or parse an operation, then delegate to the focused script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interactive:
        args = _interactive_args(args=args, input_fn=input_fn)
    elif args.resource is None:
        parser.error("the following arguments are required: {account,guest} or --interactive")
    return _dispatch(args)


def _dispatch(args: argparse.Namespace) -> int:
    base_argv = env_argv(profile=args.env, env_file=args.env_file)
    if args.resource == "account" and args.action == "approve":
        delegated = [*base_argv, "--email", args.email]
        if args.send_email:
            delegated.append("--send-email")
        delegated.extend(["--lang", args.lang])
        return approve_account_signup.main(delegated)
    if args.resource == "account" and args.action == "reject":
        return reject_account_signup.main([*base_argv, "--email", args.email])
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


if __name__ == "__main__":
    raise SystemExit(main())
