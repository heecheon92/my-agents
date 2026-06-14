"""Persist KB source selection and require documents to belong to a KB.

Revision ID: 20260522_0009
Revises: 20260522_0008
Create Date: 2026-05-22
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "20260522_0009"
down_revision = "20260522_0008"
branch_labels = None
depends_on = None


def _backfill_legacy_document_knowledge_bases() -> None:
    """Attach pre-KB documents to generated KB containers before enforcing NOT NULL."""
    connection = op.get_bind()
    legacy_groups = connection.execute(
        sa.text(
            """
            select owner_user_id, group_id, min(created_at) as created_at
            from documents
            where knowledge_base_id is null
            group by owner_user_id, group_id
            """
        )
    ).mappings()

    for legacy_group in legacy_groups:
        knowledge_base_id = str(uuid.uuid4())
        group_id = legacy_group["group_id"]
        scope = "group" if group_id else "personal"
        now = datetime.now(UTC)
        created_at = legacy_group["created_at"] or now
        connection.execute(
            sa.text(
                """
                insert into knowledge_bases (id, name, scope, owner_user_id, group_id, created_at)
                values (:id, :name, :scope, :owner_user_id, :group_id, :created_at)
                """
            ),
            {
                "id": knowledge_base_id,
                "name": "Migrated legacy knowledge",
                "scope": scope,
                "owner_user_id": legacy_group["owner_user_id"],
                "group_id": group_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            sa.text(
                """
                update documents
                set knowledge_base_id = :knowledge_base_id
                where knowledge_base_id is null
                  and owner_user_id = :owner_user_id
                  and (
                    (:group_id is null and group_id is null)
                    or group_id = :group_id
                  )
                """
            ),
            {
                "knowledge_base_id": knowledge_base_id,
                "owner_user_id": legacy_group["owner_user_id"],
                "group_id": group_id,
            },
        )


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
    if not op.get_context().as_sql:
        _backfill_legacy_document_knowledge_bases()
    if op.get_context().as_sql:
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
    if op.get_context().as_sql:
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
