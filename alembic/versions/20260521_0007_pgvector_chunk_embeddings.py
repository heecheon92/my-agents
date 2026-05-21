"""Add pgvector storage for accelerated authorized retrieval.

Revision ID: 20260521_0007
Revises: 20260521_0006
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "20260521_0007"
down_revision = "20260521_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "document_chunks",
        sa.Column(
            "embedding_vector",
            Vector().with_variant(sa.Text(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "embedding_vector")
