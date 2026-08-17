"""Add run-scoped LangGraph interaction metadata.

Revision ID: 20260817_0032
Revises: 20260809_0031
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0032"
down_revision: str | Sequence[str] | None = "20260809_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("graph_version", sa.String(length=80), nullable=True))
    op.add_column("agent_runs", sa.Column("interaction_id", sa.String(length=80), nullable=True))
    op.add_column("agent_runs", sa.Column("interaction_type", sa.String(length=80), nullable=True))
    op.add_column("agent_runs", sa.Column("interaction_payload_json", sa.Text(), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("interaction_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("agent_runs", sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_agent_runs_interaction_id", "agent_runs", ["interaction_id"], unique=False)
    op.create_index(
        "ix_agent_runs_interaction_expires_at",
        "agent_runs",
        ["interaction_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_interaction_expires_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_interaction_id", table_name="agent_runs")
    op.drop_column("agent_runs", "resumed_at")
    op.drop_column("agent_runs", "interaction_expires_at")
    op.drop_column("agent_runs", "interaction_payload_json")
    op.drop_column("agent_runs", "interaction_type")
    op.drop_column("agent_runs", "interaction_id")
    op.drop_column("agent_runs", "graph_version")
