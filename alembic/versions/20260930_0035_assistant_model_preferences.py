"""Persist user assistant-model preferences and pin the model on admitted runs."""

import sqlalchemy as sa

from alembic import op

revision = "20260930_0035"
down_revision = "20260905_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("assistant_model_preference", sa.String(80), nullable=True))
    op.add_column("agent_runs", sa.Column("assistant_model", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "assistant_model")
    op.drop_column("users", "assistant_model_preference")
