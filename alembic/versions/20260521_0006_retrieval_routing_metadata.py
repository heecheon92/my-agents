"""Persist retrieval routing metadata on conversation runs.

Revision ID: 20260521_0006
Revises: 20260521_0005
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260521_0006"
down_revision = "20260521_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("retrieval_route", sa.String(length=40), nullable=True))
    op.add_column("agent_runs", sa.Column("answer_mode", sa.String(length=40), nullable=True))
    op.add_column("agent_runs", sa.Column("document_scope", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "document_scope")
    op.drop_column("agent_runs", "answer_mode")
    op.drop_column("agent_runs", "retrieval_route")
