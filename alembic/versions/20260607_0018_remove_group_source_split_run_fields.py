"""Remove deprecated group-source split fields from runs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260607_0018"
down_revision: str | Sequence[str] | None = "20260607_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql or context.dialect.name != "sqlite":
        op.drop_column("agent_runs", "optional_personal_knowledge_base_ids_json")
        op.drop_column("agent_runs", "mandatory_group_knowledge_base_ids_json")
        op.drop_column("agent_runs", "source_context_group_id")
        return

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_column("optional_personal_knowledge_base_ids_json")
        batch_op.drop_column("mandatory_group_knowledge_base_ids_json")
        batch_op.drop_column("source_context_group_id")


def downgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("source_context_group_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("mandatory_group_knowledge_base_ids_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("optional_personal_knowledge_base_ids_json", sa.Text(), nullable=True),
    )
