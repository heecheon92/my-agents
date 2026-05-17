"""Authorization contracts for permission-aware service modules."""

from __future__ import annotations

from enum import StrEnum


class DocumentOperation(StrEnum):
    """Document operations protected by authorization checks."""

    READ = "read"
    WRITE = "write"
    MANAGE_PERMISSIONS = "manage_permissions"
    INGEST = "ingest"
    RETRIEVE = "retrieve"
    CITE = "cite"
