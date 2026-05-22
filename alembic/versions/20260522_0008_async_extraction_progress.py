"""Add async extraction run progress fields.

Revision ID: 20260522_0008
Revises: 20260521_0007
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260522_0008"
down_revision = "20260521_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_runs", sa.Column("stage", sa.String(length=40), nullable=True))
    op.add_column(
        "extraction_runs",
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("entity_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("relationship_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_runs", "completed_at")
    op.drop_column("extraction_runs", "started_at")
    op.drop_column("extraction_runs", "relationship_count")
    op.drop_column("extraction_runs", "entity_count")
    op.drop_column("extraction_runs", "chunk_count")
    op.drop_column("extraction_runs", "progress_percent")
    op.drop_column("extraction_runs", "stage")
