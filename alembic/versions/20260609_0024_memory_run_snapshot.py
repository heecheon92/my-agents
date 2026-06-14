"""Add redacted memory source snapshots to runs.

Revision ID: 20260609_0024
Revises: 20260609_0023
Create Date: 2026-06-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260609_0024"
down_revision: str | Sequence[str] | None = "20260609_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("memory_source_snapshot_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "memory_source_snapshot_json")
