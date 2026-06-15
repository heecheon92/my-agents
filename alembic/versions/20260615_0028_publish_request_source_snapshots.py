"""Preserve publish request source snapshots across document deletion.

Revision ID: 20260615_0028
Revises: 20260614_0027
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260615_0028"
down_revision = "20260614_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_publish_requests",
        sa.Column("source_document_title_snapshot", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "knowledge_publish_requests",
        sa.Column("source_document_excerpt_snapshot", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_publish_requests",
        sa.Column("source_document_filename_snapshot", sa.String(length=512), nullable=True),
    )
    if not op.get_context().as_sql:
        op.execute(
            sa.text(
                """
                UPDATE knowledge_publish_requests
                SET
                    source_document_title_snapshot = (
                        SELECT documents.title
                        FROM documents
                        WHERE documents.id = knowledge_publish_requests.source_document_id
                    ),
                    source_document_excerpt_snapshot = (
                        SELECT substr(documents.content, 1, 500)
                        FROM documents
                        WHERE documents.id = knowledge_publish_requests.source_document_id
                    ),
                    source_document_filename_snapshot = (
                        SELECT documents.source_filename
                        FROM documents
                        WHERE documents.id = knowledge_publish_requests.source_document_id
                    )
                WHERE source_document_id IS NOT NULL
                """
            )
        )


def downgrade() -> None:
    op.drop_column("knowledge_publish_requests", "source_document_filename_snapshot")
    op.drop_column("knowledge_publish_requests", "source_document_excerpt_snapshot")
    op.drop_column("knowledge_publish_requests", "source_document_title_snapshot")
