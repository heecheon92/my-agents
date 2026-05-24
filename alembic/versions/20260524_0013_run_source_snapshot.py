"""Persist run source snapshots for regeneration warnings.

Revision ID: 20260524_0013
Revises: 20260524_0012
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260524_0013"
down_revision = "20260524_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("retrieval_source_snapshot_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "retrieval_source_snapshot_json")
