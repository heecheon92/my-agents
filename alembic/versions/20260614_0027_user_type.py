"""Add platform user type for system knowledge management."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_0027"
down_revision: str | Sequence[str] | None = "20260614_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("user_type", sa.String(length=20), nullable=False, server_default="normal"),
    )
    op.create_index(op.f("ix_users_user_type"), "users", ["user_type"])


def downgrade() -> None:
    op.drop_index(op.f("ix_users_user_type"), table_name="users")
    op.drop_column("users", "user_type")
