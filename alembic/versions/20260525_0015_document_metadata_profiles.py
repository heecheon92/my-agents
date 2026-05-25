"""Add generated document metadata profiles for retrieval.

Revision ID: 20260525_0015
Revises: 20260524_0014
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "20260525_0015"
down_revision = "20260524_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_metadata_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("extraction_run_id", sa.String(length=36), nullable=False),
        sa.Column("generated_title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("keywords_json", sa.Text(), nullable=False),
        sa.Column("topics_json", sa.Text(), nullable=False),
        sa.Column("entities_json", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("generator", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column(
            "embedding_vector",
            Vector().with_variant(sa.Text(), "sqlite"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_metadata_profiles_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            name=op.f("fk_document_metadata_profiles_extraction_run_id_extraction_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_metadata_profiles")),
    )
    op.create_index(
        op.f("ix_document_metadata_profiles_document_id"),
        "document_metadata_profiles",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_metadata_profiles_extraction_run_id"),
        "document_metadata_profiles",
        ["extraction_run_id"],
        unique=False,
    )


def downgrade() -> None:
    table_name = "document_metadata_profiles"
    op.drop_index(op.f("ix_document_metadata_profiles_extraction_run_id"), table_name=table_name)
    op.drop_index(op.f("ix_document_metadata_profiles_document_id"), table_name=table_name)
    op.drop_table(table_name)
