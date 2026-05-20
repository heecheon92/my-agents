"""Add refresh-safe run detail fields."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260520_0003"
down_revision: str | Sequence[str] | None = "20260518_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("route_explanation", sa.Text(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("assistant_message_id", sa.String(length=36), nullable=True)
    )
    op.create_index(
        op.f("ix_agent_runs_assistant_message_id"),
        "agent_runs",
        ["assistant_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_runs_assistant_message_id"), table_name="agent_runs")
    op.drop_column("agent_runs", "assistant_message_id")
    op.drop_column("agent_runs", "route_explanation")
