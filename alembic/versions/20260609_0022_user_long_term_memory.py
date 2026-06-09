"""Add opt-in per-user long-term memory tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260609_0022"
down_revision: str | Sequence[str] | None = "20260609_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memory_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_memory_settings_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_memory_settings")),
        sa.UniqueConstraint("user_id", name="uq_user_memory_settings_user_id"),
    )
    op.create_index(
        op.f("ix_user_memory_settings_user_id"),
        "user_memory_settings",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "user_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_json", sa.Text(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provenance_type", sa.String(length=40), nullable=False),
        sa.Column("source_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("source_message_id", sa.String(length=36), nullable=True),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("source_document_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_memories_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_memories")),
        sa.UniqueConstraint("user_id", "key", name="uq_user_memories_user_id_key"),
    )
    for column in (
        "category",
        "key",
        "provenance_type",
        "source_conversation_id",
        "source_document_id",
        "source_message_id",
        "source_run_id",
        "status",
        "user_id",
    ):
        op.create_index(op.f(f"ix_user_memories_{column}"), "user_memories", [column], unique=False)


def downgrade() -> None:
    for column in (
        "user_id",
        "status",
        "source_run_id",
        "source_message_id",
        "source_document_id",
        "source_conversation_id",
        "provenance_type",
        "key",
        "category",
    ):
        op.drop_index(op.f(f"ix_user_memories_{column}"), table_name="user_memories")
    op.drop_table("user_memories")
    op.drop_index(op.f("ix_user_memory_settings_user_id"), table_name="user_memory_settings")
    op.drop_table("user_memory_settings")
