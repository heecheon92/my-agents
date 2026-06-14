"""Add memory write-policy lifecycle fields."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260609_0023"
down_revision: str | Sequence[str] | None = "20260609_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_memories",
        sa.Column(
            "sensitivity", sa.String(length=20), nullable=False, server_default="non_sensitive"
        ),
    )
    op.add_column("user_memories", sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_memories", sa.Column("stale_reason", sa.String(length=120), nullable=True))
    op.create_index(
        op.f("ix_user_memories_sensitivity"), "user_memories", ["sensitivity"], unique=False
    )
    op.create_table(
        "memory_suggestions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sensitivity", sa.String(length=20), nullable=False),
        sa.Column("source_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("source_message_id", sa.String(length=36), nullable=True),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("source_document_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("memory_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memory_suggestions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_suggestions")),
    )
    for column in (
        "category",
        "expires_at",
        "memory_id",
        "sensitivity",
        "source_conversation_id",
        "source_document_id",
        "source_message_id",
        "source_run_id",
        "status",
        "user_id",
    ):
        op.create_index(
            op.f(f"ix_memory_suggestions_{column}"),
            "memory_suggestions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in (
        "user_id",
        "status",
        "source_run_id",
        "source_message_id",
        "source_document_id",
        "source_conversation_id",
        "sensitivity",
        "memory_id",
        "expires_at",
        "category",
    ):
        op.drop_index(op.f(f"ix_memory_suggestions_{column}"), table_name="memory_suggestions")
    op.drop_table("memory_suggestions")
    op.drop_index(op.f("ix_user_memories_sensitivity"), table_name="user_memories")
    op.drop_column("user_memories", "stale_reason")
    op.drop_column("user_memories", "stale_at")
    op.drop_column("user_memories", "sensitivity")
