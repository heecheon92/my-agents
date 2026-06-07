"""Remove deprecated conversation group context."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260607_0019"
down_revision: str | Sequence[str] | None = "20260607_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql or context.dialect.name != "sqlite":
        op.drop_column("conversations", "group_id")
        return

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_index("ix_conversations_group_id")
        batch_op.drop_column("group_id")


def downgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("group_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_conversations_group_id",
        "conversations",
        ["group_id"],
        unique=False,
    )
