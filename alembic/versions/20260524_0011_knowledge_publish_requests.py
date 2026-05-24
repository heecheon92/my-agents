"""Add knowledge publish request review workflow.

Revision ID: 20260524_0011
Revises: 20260524_0010
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260524_0011"
down_revision = "20260524_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_publish_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requester_user_id", sa.String(length=36), nullable=False),
        sa.Column("target_group_id", sa.String(length=36), nullable=False),
        sa.Column("target_knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reviewer_user_id", sa.String(length=36), nullable=True),
        sa.Column("published_document_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["target_group_id"],
            ["groups.id"],
            name=op.f("fk_knowledge_publish_requests_target_group_id_groups"),
        ),
        sa.ForeignKeyConstraint(
            ["target_knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_publish_requests_target_knowledge_base_id_knowledge_bases"),
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name=op.f("fk_knowledge_publish_requests_source_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["published_document_id"],
            ["documents.id"],
            name=op.f("fk_knowledge_publish_requests_published_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_publish_requests")),
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_published_document_id"),
        "knowledge_publish_requests",
        ["published_document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_requester_user_id"),
        "knowledge_publish_requests",
        ["requester_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_reviewer_user_id"),
        "knowledge_publish_requests",
        ["reviewer_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_source_document_id"),
        "knowledge_publish_requests",
        ["source_document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_status"),
        "knowledge_publish_requests",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_target_group_id"),
        "knowledge_publish_requests",
        ["target_group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_target_knowledge_base_id"),
        "knowledge_publish_requests",
        ["target_knowledge_base_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_knowledge_publish_requests_target_knowledge_base_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_target_group_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_status"),
        table_name="knowledge_publish_requests",
    )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_source_document_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_reviewer_user_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_requester_user_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_published_document_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_table("knowledge_publish_requests")
