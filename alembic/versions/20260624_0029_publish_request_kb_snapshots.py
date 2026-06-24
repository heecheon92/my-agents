"""Add knowledge-base name snapshots to publish requests.

Revision ID: 20260624_0029
Revises: 20260615_0028
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260624_0029"
down_revision = "20260615_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_publish_requests",
        sa.Column("source_knowledge_base_name_snapshot", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "knowledge_publish_requests",
        sa.Column("published_knowledge_base_name_snapshot", sa.String(length=160), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE knowledge_publish_requests
            SET source_knowledge_base_name_snapshot = (
                SELECT knowledge_bases.name
                FROM knowledge_bases
                WHERE knowledge_bases.id = knowledge_publish_requests.source_knowledge_base_id
            )
            WHERE source_knowledge_base_id IS NOT NULL
              AND source_knowledge_base_name_snapshot IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE knowledge_publish_requests
            SET published_knowledge_base_name_snapshot = (
                SELECT knowledge_bases.name
                FROM knowledge_bases
                WHERE knowledge_bases.id = knowledge_publish_requests.published_knowledge_base_id
            )
            WHERE published_knowledge_base_id IS NOT NULL
              AND published_knowledge_base_name_snapshot IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("knowledge_publish_requests", "published_knowledge_base_name_snapshot")
    op.drop_column("knowledge_publish_requests", "source_knowledge_base_name_snapshot")
