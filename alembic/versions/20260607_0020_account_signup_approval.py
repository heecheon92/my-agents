"""Add account signup approval state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260607_0020"
down_revision: str | Sequence[str] | None = "20260607_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "approval_status",
            sa.String(length=20),
            server_default="approved",
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f("ix_users_approval_status"),
        "users",
        ["approval_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_approval_status"), table_name="users")
    op.drop_column("users", "rejected_at")
    op.drop_column("users", "approved_at")
    op.drop_column("users", "approval_status")
