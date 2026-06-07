"""Backward-compatible entrypoint for auth approval operator workflows.

Prefer `scripts.ops` for new interactive operational workflows.
"""

from __future__ import annotations

from scripts.approve_account_signup import AccountApprovalCliResult
from scripts.issue_guest_access_code import GuestCodeIssueResult as GuestCodeIssueCliResult
from scripts.ops import main

__all__ = ["AccountApprovalCliResult", "GuestCodeIssueCliResult", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
