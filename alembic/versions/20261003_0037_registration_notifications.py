"""Durable operator registration notifications."""

import sqlalchemy as sa

from alembic import op

revision = "20261003_0037"
down_revision = "20260930_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registration_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column("account_email", sa.String(320), nullable=True),
        sa.Column("approval_status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guest_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_registration_notifications_user_id"),
    )

    op.create_index(
        "ix_registration_notifications_pending",
        "registration_notifications",
        ["sent_at", "available_at"],
    )


def downgrade() -> None:
    op.drop_table("registration_notifications")
