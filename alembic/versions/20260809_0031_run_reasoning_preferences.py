"""Persist effective reasoning preferences on conversation runs.

Revision ID: 20260809_0031
Revises: 20260809_0030
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260809_0031"
down_revision = "20260809_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "reasoning_mode",
            sa.String(length=20),
            nullable=False,
            server_default="standard",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "reasoning_effort",
            sa.String(length=20),
            nullable=False,
            server_default="medium",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "reasoning_effort")
    op.drop_column("agent_runs", "reasoning_mode")
