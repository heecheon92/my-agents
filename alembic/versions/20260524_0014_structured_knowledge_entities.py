"""Add structured knowledge entities for ContextForge retrieval.

Revision ID: 20260524_0014
Revises: 20260524_0013
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260524_0014"
down_revision = "20260524_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "structured_knowledge_entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("extraction_run_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("attributes_json", sa.Text(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_structured_knowledge_entities_chunk_id_document_chunks"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_structured_knowledge_entities_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["extraction_runs.id"],
            name=op.f("fk_structured_knowledge_entities_extraction_run_id_extraction_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_structured_knowledge_entities")),
    )
    op.create_index(
        op.f("ix_structured_knowledge_entities_chunk_id"),
        "structured_knowledge_entities",
        ["chunk_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structured_knowledge_entities_document_id"),
        "structured_knowledge_entities",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structured_knowledge_entities_entity_type"),
        "structured_knowledge_entities",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structured_knowledge_entities_extraction_run_id"),
        "structured_knowledge_entities",
        ["extraction_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structured_knowledge_entities_label"),
        "structured_knowledge_entities",
        ["label"],
        unique=False,
    )


def downgrade() -> None:
    table_name = "structured_knowledge_entities"
    op.drop_index(op.f("ix_structured_knowledge_entities_label"), table_name=table_name)
    op.drop_index(
        op.f("ix_structured_knowledge_entities_extraction_run_id"),
        table_name=table_name,
    )
    op.drop_index(op.f("ix_structured_knowledge_entities_entity_type"), table_name=table_name)
    op.drop_index(op.f("ix_structured_knowledge_entities_document_id"), table_name=table_name)
    op.drop_index(op.f("ix_structured_knowledge_entities_chunk_id"), table_name=table_name)
    op.drop_table("structured_knowledge_entities")
