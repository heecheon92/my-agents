"""Retain private source-linked evidence for conversation follow-ups."""

import sqlalchemy as sa

from alembic import op

revision = "20261018_0043"
down_revision = "20261018_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("session_evidence_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "session_evidence_json")
