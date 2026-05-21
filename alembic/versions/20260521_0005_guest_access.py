"""Add provider-free guest access state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260521_0005"
down_revision: str | Sequence[str] | None = "20260520_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "account_type",
            sa.String(length=20),
            nullable=False,
            server_default="registered",
        ),
    )
    op.add_column(
        "users",
        sa.Column("guest_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "guest_access_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["guest_user_id"],
            ["users.id"],
            name=op.f("fk_guest_access_codes_guest_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guest_access_codes")),
        sa.UniqueConstraint("code_hash", name="uq_guest_access_codes_code_hash"),
    )
    op.create_index(
        op.f("ix_guest_access_codes_code_hash"),
        "guest_access_codes",
        ["code_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_guest_access_codes_guest_user_id"),
        "guest_access_codes",
        ["guest_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_guest_access_codes_guest_user_id"), table_name="guest_access_codes")
    op.drop_index(op.f("ix_guest_access_codes_code_hash"), table_name="guest_access_codes")
    op.drop_table("guest_access_codes")
    op.drop_column("sessions", "expires_at")
    op.drop_column("users", "guest_expires_at")
    op.drop_column("users", "account_type")
