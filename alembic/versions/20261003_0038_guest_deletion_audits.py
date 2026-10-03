"""Content-free audit for expired guest account erasure."""

import sqlalchemy as sa

from alembic import op

revision = "20261003_0038"
down_revision = "20261003_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_deletion_audits",
        sa.Column("guest_user_id", sa.String(36), primary_key=True),
        sa.Column("email_fingerprint", sa.String(64), nullable=True),
        sa.Column("guest_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guest_expired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_guest_deletion_audits_email_fingerprint", "guest_deletion_audits", ["email_fingerprint"]
    )


def downgrade() -> None:
    op.drop_table("guest_deletion_audits")
