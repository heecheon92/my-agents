"""Authentication boundary contracts for first-party auth."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

SessionCookieSameSite = Literal["lax", "strict", "none"]


class UserType(StrEnum):
    """Platform privilege type, separate from registered-vs-guest account type."""

    NORMAL = "normal"
    ROOT = "root"
    SYSTEM = "system"


SYSTEM_KNOWLEDGE_MANAGER_USER_TYPES = frozenset({UserType.ROOT.value, UserType.SYSTEM.value})


def can_manage_system_knowledge_for_user_type(user_type: str | None) -> bool:
    """Return whether a stored user_type has system knowledge management rights."""
    return (user_type or UserType.NORMAL.value) in SYSTEM_KNOWLEDGE_MANAGER_USER_TYPES


@dataclass(frozen=True)
class Principal:
    """Authenticated application principal passed into protected services."""

    user_id: str
    session_id: str
    is_guest: bool = False
    user_type: str = UserType.NORMAL.value

    @property
    def can_manage_system_knowledge(self) -> bool:
        """Return whether the principal may manage public system knowledge sources."""
        if self.is_guest:
            return False
        return can_manage_system_knowledge_for_user_type(self.user_type)
