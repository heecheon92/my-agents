"""Add PDF upload metadata and chunk page provenance."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260520_0004"
down_revision: str | Sequence[str] | None = "20260520_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="text"),
    )
    op.add_column("documents", sa.Column("source_filename", sa.String(length=255), nullable=True))
    op.add_column(
        "documents", sa.Column("source_content_type", sa.String(length=120), nullable=True)
    )
    op.add_column("documents", sa.Column("source_byte_size", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("source_sha256", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("source_page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("parser_name", sa.String(length=80), nullable=True))
    op.add_column("document_chunks", sa.Column("source_page", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "source_page")
    op.drop_column("documents", "parser_name")
    op.drop_column("documents", "source_page_count")
    op.drop_column("documents", "source_sha256")
    op.drop_column("documents", "source_byte_size")
    op.drop_column("documents", "source_content_type")
    op.drop_column("documents", "source_filename")
    op.drop_column("documents", "source_type")
