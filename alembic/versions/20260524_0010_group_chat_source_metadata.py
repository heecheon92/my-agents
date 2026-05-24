"""Persist group-chat source context metadata.

Revision ID: 20260524_0010
Revises: 20260522_0009
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260524_0010"
down_revision = "20260522_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("source_context_group_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "agent_runs",
        sa.Column("mandatory_group_knowledge_base_ids_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("optional_personal_knowledge_base_ids_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("resolved_knowledge_base_ids_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "resolved_knowledge_base_ids_json")
    op.drop_column("agent_runs", "optional_personal_knowledge_base_ids_json")
    op.drop_column("agent_runs", "mandatory_group_knowledge_base_ids_json")
    op.drop_column("agent_runs", "source_context_group_id")
