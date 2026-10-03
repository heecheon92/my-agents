"""Durable email trial eligibility and shared guest rate limits."""

import sqlalchemy as sa

from alembic import op

revision = "20261003_0039"
down_revision = "20261003_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_trials",
        sa.Column("email_fingerprint", sa.String(64), primary_key=True),
        sa.Column("guest_user_id", sa.String(36), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_guest_trials_guest_user_id", "guest_trials", ["guest_user_id"])
    op.create_table(
        "guest_policy_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key_verifier", sa.String(64), nullable=False),
        sa.Column("initialized_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "guest_rate_buckets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_guest_rate_buckets_expires_at", "guest_rate_buckets", ["expires_at"])
    op.add_column(
        "guest_access_codes", sa.Column("email_fingerprint", sa.String(64), nullable=True)
    )
    op.add_column(
        "guest_access_codes",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_guest_access_codes_email_fingerprint", "guest_access_codes", ["email_fingerprint"]
    )


def downgrade() -> None:
    op.drop_index("ix_guest_access_codes_email_fingerprint", table_name="guest_access_codes")
    op.drop_column("guest_access_codes", "generation")
    op.drop_column("guest_access_codes", "email_fingerprint")
    op.drop_table("guest_rate_buckets")
    op.drop_table("guest_policy_state")
    op.drop_table("guest_trials")
