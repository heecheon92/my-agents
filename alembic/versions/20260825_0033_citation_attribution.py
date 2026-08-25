"""Add answer-use attribution to persisted citation evidence.

Revision ID: 20260825_0033
Revises: 20260817_0032
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_0033"
down_revision: str | Sequence[str] | None = "20260817_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("citation_attribution_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "citations",
        sa.Column("used_in_answer", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("citations", "used_in_answer")
    op.drop_column("agent_runs", "citation_attribution_version")
