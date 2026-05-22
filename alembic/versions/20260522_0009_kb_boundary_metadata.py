"""Persist KB source selection and require documents to belong to a KB.

Revision ID: 20260522_0009
Revises: 20260522_0008
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from alembic.runtime import migration

revision = "20260522_0009"
down_revision = "20260522_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("knowledge_base_selection_mode", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("selected_knowledge_base_ids_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "resolved_knowledge_base_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    if migration.MigrationContext.get_current().as_sql:
        op.alter_column(
            "documents",
            "knowledge_base_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
    else:
        with op.batch_alter_table("documents") as batch_op:
            batch_op.alter_column(
                "knowledge_base_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )


def downgrade() -> None:
    if migration.MigrationContext.get_current().as_sql:
        op.alter_column(
            "documents",
            "knowledge_base_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
    else:
        with op.batch_alter_table("documents") as batch_op:
            batch_op.alter_column(
                "knowledge_base_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
    op.drop_column("agent_runs", "resolved_knowledge_base_count")
    op.drop_column("agent_runs", "selected_knowledge_base_ids_json")
    op.drop_column("agent_runs", "knowledge_base_selection_mode")
