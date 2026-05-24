"""Allow approved personal KB publications into groups.

Revision ID: 20260524_0012
Revises: 20260524_0011
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260524_0012"
down_revision = "20260524_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        op.add_column(
            "knowledge_publish_requests",
            sa.Column("source_knowledge_base_id", sa.String(length=36), nullable=True),
        )
        op.add_column(
            "knowledge_publish_requests",
            sa.Column("published_knowledge_base_id", sa.String(length=36), nullable=True),
        )
        op.alter_column(
            "knowledge_publish_requests",
            "target_knowledge_base_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        op.alter_column(
            "knowledge_publish_requests",
            "source_document_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
    else:
        with op.batch_alter_table("knowledge_publish_requests") as batch_op:
            batch_op.add_column(
                sa.Column("source_knowledge_base_id", sa.String(length=36), nullable=True)
            )
            batch_op.add_column(
                sa.Column("published_knowledge_base_id", sa.String(length=36), nullable=True)
            )
            batch_op.create_foreign_key(
                op.f("fk_knowledge_publish_requests_source_knowledge_base_id_knowledge_bases"),
                "knowledge_bases",
                ["source_knowledge_base_id"],
                ["id"],
            )
            batch_op.create_foreign_key(
                op.f("fk_knowledge_publish_requests_published_knowledge_base_id_knowledge_bases"),
                "knowledge_bases",
                ["published_knowledge_base_id"],
                ["id"],
            )
            batch_op.alter_column(
                "target_knowledge_base_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
            batch_op.alter_column(
                "source_document_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
    op.create_index(
        op.f("ix_knowledge_publish_requests_source_knowledge_base_id"),
        "knowledge_publish_requests",
        ["source_knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_publish_requests_published_knowledge_base_id"),
        "knowledge_publish_requests",
        ["published_knowledge_base_id"],
        unique=False,
    )

    op.create_table(
        "knowledge_base_publications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("requester_user_id", sa.String(length=36), nullable=False),
        sa.Column("approved_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("publish_request_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_knowledge_base_publications_group_id_groups"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_base_publications_knowledge_base_id_knowledge_bases"),
        ),
        sa.ForeignKeyConstraint(
            ["publish_request_id"],
            ["knowledge_publish_requests.id"],
            name=op.f(
                "fk_knowledge_base_publications_publish_request_id_knowledge_publish_requests"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_base_publications")),
        sa.UniqueConstraint(
            "group_id",
            "knowledge_base_id",
            name="uq_kb_publication_group_kb",
        ),
    )
    op.create_index(
        op.f("ix_knowledge_base_publications_approved_by_user_id"),
        "knowledge_base_publications",
        ["approved_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_base_publications_group_id"),
        "knowledge_base_publications",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_base_publications_knowledge_base_id"),
        "knowledge_base_publications",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_base_publications_publish_request_id"),
        "knowledge_base_publications",
        ["publish_request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_base_publications_requester_user_id"),
        "knowledge_base_publications",
        ["requester_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_knowledge_base_publications_requester_user_id"),
        table_name="knowledge_base_publications",
    )
    op.drop_index(
        op.f("ix_knowledge_base_publications_publish_request_id"),
        table_name="knowledge_base_publications",
    )
    op.drop_index(
        op.f("ix_knowledge_base_publications_knowledge_base_id"),
        table_name="knowledge_base_publications",
    )
    op.drop_index(
        op.f("ix_knowledge_base_publications_group_id"),
        table_name="knowledge_base_publications",
    )
    op.drop_index(
        op.f("ix_knowledge_base_publications_approved_by_user_id"),
        table_name="knowledge_base_publications",
    )
    op.drop_table("knowledge_base_publications")
    if op.get_context().as_sql:
        op.alter_column(
            "knowledge_publish_requests",
            "source_document_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
        op.alter_column(
            "knowledge_publish_requests",
            "target_knowledge_base_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
    else:
        with op.batch_alter_table("knowledge_publish_requests") as batch_op:
            batch_op.alter_column(
                "source_document_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
            batch_op.alter_column(
                "target_knowledge_base_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_published_knowledge_base_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_index(
        op.f("ix_knowledge_publish_requests_source_knowledge_base_id"),
        table_name="knowledge_publish_requests",
    )
    op.drop_constraint(
        op.f("fk_knowledge_publish_requests_published_knowledge_base_id_knowledge_bases"),
        "knowledge_publish_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_knowledge_publish_requests_source_knowledge_base_id_knowledge_bases"),
        "knowledge_publish_requests",
        type_="foreignkey",
    )
    op.drop_column("knowledge_publish_requests", "published_knowledge_base_id")
    op.drop_column("knowledge_publish_requests", "source_knowledge_base_id")
