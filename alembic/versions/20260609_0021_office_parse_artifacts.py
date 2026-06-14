"""Add parse artifacts and generic source-location provenance."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260609_0021"
down_revision: str | Sequence[str] | None = "20260607_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_parse_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_content_type", sa.String(length=120), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("parser_provider", sa.String(length=80), nullable=False),
        sa.Column("parser_name", sa.String(length=120), nullable=False),
        sa.Column("parser_version", sa.String(length=80), nullable=True),
        sa.Column("parser_mode", sa.String(length=80), nullable=True),
        sa.Column("markdown_content", sa.Text(), nullable=False),
        sa.Column("elements_json", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_parse_artifacts_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_parse_artifacts")),
    )
    op.create_index(
        op.f("ix_document_parse_artifacts_document_id"),
        "document_parse_artifacts",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_parse_artifacts_parser_name"),
        "document_parse_artifacts",
        ["parser_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_parse_artifacts_source_sha256"),
        "document_parse_artifacts",
        ["source_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_parse_artifacts_source_type"),
        "document_parse_artifacts",
        ["source_type"],
        unique=False,
    )
    op.add_column(
        "document_chunks",
        sa.Column("source_location_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "structured_knowledge_entities",
        sa.Column("source_location_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("structured_knowledge_entities", "source_location_json")
    op.drop_column("document_chunks", "source_location_json")
    op.drop_index(
        op.f("ix_document_parse_artifacts_source_type"),
        table_name="document_parse_artifacts",
    )
    op.drop_index(
        op.f("ix_document_parse_artifacts_source_sha256"),
        table_name="document_parse_artifacts",
    )
    op.drop_index(
        op.f("ix_document_parse_artifacts_parser_name"),
        table_name="document_parse_artifacts",
    )
    op.drop_index(
        op.f("ix_document_parse_artifacts_document_id"),
        table_name="document_parse_artifacts",
    )
    op.drop_table("document_parse_artifacts")
