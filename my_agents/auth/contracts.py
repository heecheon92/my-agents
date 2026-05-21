"""Authentication boundary contracts for first-party auth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SessionCookieSameSite = Literal["lax", "strict", "none"]


@dataclass(frozen=True)
class Principal:
    """Authenticated application principal passed into protected services."""

    user_id: str
    session_id: str
    is_guest: bool = False
