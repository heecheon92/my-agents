"""Add group invitation lifecycle table."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260610_0025"
down_revision: str | Sequence[str] | None = "20260609_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("invited_email_normalized", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("accepted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name=op.f("fk_group_invitations_accepted_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_invitations_group_id_groups"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_invitations")),
        sa.UniqueConstraint("token_hash", name="uq_group_invitations_token_hash"),
    )
    for column in (
        "accepted_by_user_id",
        "created_by_user_id",
        "group_id",
        "invited_email_normalized",
        "status",
        "token_hash",
    ):
        op.create_index(
            op.f(f"ix_group_invitations_{column}"),
            "group_invitations",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_group_invitations_group_status_email",
        "group_invitations",
        ["group_id", "status", "invited_email_normalized"],
        unique=False,
    )
    op.create_index(
        "uq_group_invitations_pending_email",
        "group_invitations",
        ["group_id", "invited_email_normalized"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_group_invitations_pending_email", table_name="group_invitations")
    op.drop_index("ix_group_invitations_group_status_email", table_name="group_invitations")
    for column in (
        "token_hash",
        "status",
        "invited_email_normalized",
        "group_id",
        "created_by_user_id",
        "accepted_by_user_id",
    ):
        op.drop_index(op.f(f"ix_group_invitations_{column}"), table_name="group_invitations")
    op.drop_table("group_invitations")
