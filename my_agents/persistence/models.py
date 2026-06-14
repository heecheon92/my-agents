"""Model registration helpers for runtime bootstrap and Alembic metadata."""

from __future__ import annotations


def import_all_models() -> None:
    """Import every SQLAlchemy model module so tables register on Base.metadata."""
    import my_agents.auth.models  # noqa: F401
    import my_agents.conversations.models  # noqa: F401
    import my_agents.groups.models  # noqa: F401
    import my_agents.knowledge.models  # noqa: F401
    import my_agents.memory.models  # noqa: F401
