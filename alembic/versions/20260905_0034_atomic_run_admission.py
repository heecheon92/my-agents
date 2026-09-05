"""Enforce one active run per conversation without discarding existing runs."""

import sqlalchemy as sa

from alembic import op

revision = "20260905_0034"
down_revision = "20260825_0033"
branch_labels = None
depends_on = None

_PREDICATE = "status IN ('running', 'waiting_for_input', 'cancelling')"


def upgrade() -> None:
    # Offline SQL cannot execute this diagnostic; the unique index still rejects duplicates.
    from alembic import context

    if not context.is_offline_mode():
        duplicates = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT conversation_id FROM agent_runs WHERE "
                    + _PREDICATE
                    + " GROUP BY conversation_id HAVING COUNT(*) > 1 LIMIT 1"
                )
            )
            .first()
        )
        if duplicates is not None:
            raise RuntimeError(
                "Cannot enforce active-run uniqueness: duplicate active conversations exist. "
                "Inspect agent_runs grouped by conversation_id for running/waiting_for_input/"
                "cancelling statuses, resolve competing runs explicitly, then retry upgrade. "
                "No runs were changed."
            )
    op.create_index(
        "uq_agent_runs_active_conversation",
        "agent_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(_PREDICATE),
        sqlite_where=sa.text(_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_runs_active_conversation", table_name="agent_runs")
